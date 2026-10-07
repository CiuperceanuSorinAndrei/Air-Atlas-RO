import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

POLLUTANT_NAMES = {1: "SO2", 5: "PM10", 7: "O3", 8: "NO2", 10: "CO", 6001: "PM2.5"}
EEA_TIMEZONE = timezone(timedelta(hours=1))
STATION_METADATA_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / ".cache" / "eea_station_metadata.json"
)


MAX_PARQUET_BYTES = 32 * 1024 * 1024
MAX_PARQUET_ROWS = 200_000
MAX_DECODED_BYTES = 128 * 1024 * 1024
METADATA_TTL = timedelta(days=1)
PARQUET_HOST = "eeadmz1batchservice02.blob.core.windows.net"
HTTP_HOSTS = frozenset(
    {
        PARQUET_HOST,
        "eeadmz1-downloads-api-appservice.azurewebsites.net",
        "air.discomap.eea.europa.eu",
        "discomap.eea.europa.eu",
    }
)
STATION_PATTERN = re.compile(r"RO[A-Z0-9]{1,6}\Z")
SERIES_PATTERN = re.compile(r"SPO-(RO[A-Z0-9]{1,6})_([0-9]{5})_([0-9]+)\.parquet\Z")


def validate_https_url(url: str, hosts: set[str] | frozenset[str] = HTTP_HOSTS) -> None:
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 33 for c in url):
        raise ValueError("Source URL must be text.")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Source URL is outside the permitted HTTPS endpoints.")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(req.full_url).hostname
        if host is None:
            raise ValueError("Source URL has no host.")
        validate_https_url(newurl, {host})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_http_response(
    request: urllib.request.Request, max_bytes: int
) -> tuple[bytes, str | None]:
    validate_https_url(request.full_url)
    host = urllib.parse.urlsplit(request.full_url).hostname
    if host is None:
        raise ValueError("Source URL has no host.")
    deadline = time.monotonic() + 60
    opener = urllib.request.build_opener(SafeRedirectHandler())
    with opener.open(request, timeout=30) as response:
        if response.status != 200:
            raise ValueError("Expected a complete HTTP 200 response.")
        validate_https_url(response.geturl(), {host})
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > max_bytes:
            raise ValueError("EEA response exceeds the download limit.")
        data = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("EEA download deadline exceeded.")
            chunk = response.read1(min(64 * 1024, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise ValueError("EEA response exceeds the download limit.")
        return bytes(data), response.headers.get("ETag")


def read_response(request: urllib.request.Request, max_bytes: int) -> bytes:
    return read_http_response(request, max_bytes)[0]


def finite_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"{label} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return number


def require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError(f"{label} must be nonempty bounded text.")
    return value


def validate_station_metadata(metadata: dict, station_id: str) -> None:
    if not isinstance(metadata, dict) or metadata.get("stationId") != station_id:
        raise ValueError("Station metadata identity mismatch.")
    if not STATION_PATTERN.fullmatch(station_id):
        raise ValueError("Invalid station ID.")
    require_text(metadata.get("stationName"), "Station name")
    longitude = finite_number(metadata.get("longitude"), "Longitude")
    latitude = finite_number(metadata.get("latitude"), "Latitude")
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        raise ValueError("Station coordinates are outside the geographic range.")


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Timestamp must be ISO text.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("Invalid ISO timestamp.") from error
    if result.utcoffset() is None:
        raise ValueError("Timestamp must include its UTC offset.")
    return result


def validate_observation(row: dict) -> None:
    if not isinstance(row, dict):
        raise TypeError("Observation must be an object.")
    station_id = extract_station_id(row.get("samplingPointId", ""))
    validate_station_metadata(row, station_id)
    station, pollutant, sequence = parse_series_url(row.get("sourceUrl", ""))
    parts = row["samplingPointId"].split("_")
    if station != station_id or parts[1] != pollutant or int(parts[2]) != sequence:
        raise ValueError("Observation and source series identities differ.")
    if row.get("source") != "EEA" or row.get("pollutant") != POLLUTANT_NAMES.get(
        int(pollutant)
    ):
        raise ValueError("Observation pollutant/source mismatch.")
    finite_number(row.get("value"), "Observation value")
    if row.get("unit") != ("mg.m-3" if row["pollutant"] == "CO" else "ug.m-3"):
        raise ValueError("Unsupported EEA concentration unit.")
    if type(row.get("validity")) is not int or row["validity"] not in (1, 2, 3, 4):
        raise ValueError("Invalid EEA validity.")
    verification = row.get("verification")
    if type(verification) is not int or verification not in (1, 2, 3):
        raise ValueError("Invalid EEA verification.")
    if row.get("status") != ("validated" if verification == 1 else "preliminary"):
        raise ValueError("Observation status does not match verification.")
    start, end, reported, ingested = [
        parse_timestamp(row.get(key))
        for key in ("observedFrom", "observedTo", "reportedAt", "ingestedAt")
    ]
    duration = end - start
    inferred = "hour" if duration == timedelta(hours=1) else "day"
    aggregation = row.get("aggregationType", inferred)
    if aggregation not in ("hour", "day") or duration != timedelta(
        hours=1 if aggregation == "hour" else 24
    ):
        raise ValueError("EEA aggregation does not match its hour/day interval.")
    if reported > ingested or start > ingested:
        raise ValueError("Observation was reported or started after ingestion.")
    if row.get("sourceRecordId") is not None:
        require_text(row["sourceRecordId"], "Source record ID")
    if row.get("dataCapture") is not None:
        capture = finite_number(row["dataCapture"], "Data capture")
        if not 0 <= capture <= 100:
            raise ValueError("Data capture is outside 0–100 percent.")


def validate_document(document: dict) -> None:
    if not isinstance(document, dict) or not isinstance(
        document.get("observations"), list
    ):
        raise TypeError("Snapshot must contain an observations array.")
    observations = document["observations"]
    if not observations:
        raise ValueError(
            "Import produced no observations; keeping the previous snapshot."
        )
    summary = document.get("importSummary")
    if not isinstance(summary, dict):
        raise TypeError("Missing import summary.")
    for key in ("attempted", "imported", "skipped", "failed"):
        if type(summary.get(key)) is not int or summary[key] < 0:
            raise ValueError("Import summary counts must be nonnegative integers.")
    if summary["imported"] != len(observations) or summary["attempted"] != sum(
        summary[k] for k in ("imported", "skipped", "failed")
    ):
        raise ValueError("Import summary does not match observations.")
    pairs = set()
    station_metadata = {}
    for row in observations:
        validate_observation(row)
        metadata = (row["stationName"], row["latitude"], row["longitude"])
        if (
            row["stationId"] in station_metadata
            and station_metadata[row["stationId"]] != metadata
        ):
            raise ValueError("Conflicting metadata for one physical station.")
        station_metadata[row["stationId"]] = metadata
        pair = row["stationId"], row["pollutant"]
        if pair in pairs:
            raise ValueError("Import contains duplicate station/pollutant pairs.")
        pairs.add(pair)


def atomic_write_json(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                document, temporary, ensure_ascii=False, indent=2, allow_nan=False
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def validate_raw_identity(row: dict, parquet_url: str) -> str:
    station, pollutant, sequence = parse_series_url(parquet_url)
    sampling_point = row.get("Samplingpoint")
    if sampling_point != f"RO/SPO-{station}_{pollutant}_{sequence}":
        raise ValueError("Raw sampling point does not match the source series.")
    if type(row.get("Pollutant")) is not int or row["Pollutant"] != int(pollutant):
        raise ValueError("Raw pollutant does not match the source series.")
    return station


class NoValidObservationsError(ValueError):
    pass


def to_eea_iso(value):
    if not isinstance(value, datetime):
        raise TypeError("EEA timestamp must be a datetime.")
    if value.tzinfo is not None:
        raise ValueError("Expected the EEA provider's timezone-naive UTC+1 timestamp.")
    return value.replace(tzinfo=EEA_TIMEZONE).isoformat()


def fetch_parquet_urls(country: str, pollutants: list[str]) -> list[str]:
    payload = {
        "countries": [country],
        "cities": [],
        "pollutants": pollutants,
        "dataset": 1,
        "source": "Atlasul Aerului Importer",
    }

    request_body = json.dumps(payload).encode("utf-8")
    url = "https://eeadmz1-downloads-api-appservice.azurewebsites.net/ParquetFile/urls"
    request = urllib.request.Request(
        url,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(3):
        try:
            response_body = read_response(request, 2 * 1024 * 1024)
            break
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt == 2 or (
                isinstance(error, urllib.error.HTTPError) and error.code < 500
            ):
                raise
            time.sleep(2**attempt)

    else:
        raise RuntimeError("Discovery retry limit exhausted.")

    response_text = response_body.decode("utf-8-sig")
    lines = response_text.splitlines()
    urls = []

    if not lines or lines[0] != "ParquetFileUrl":
        raise ValueError("Unexpected EEA response format.")

    for line in lines[1:]:
        line = line.strip()
        if line:
            parse_series_url(line)
            urls.append(line)

    if not urls:
        raise ValueError("Urls are not available.")

    return urls


def select_latest_valid_observation(table: pa.Table) -> dict:
    validity_mask = pc.call_function(
        "is_in",
        [table["Validity"]],
        options=pc.SetLookupOptions(value_set=pa.array([1, 2, 3, 4])),
    )
    valid_rows = table.filter(validity_mask)
    if valid_rows.num_rows == 0:
        raise NoValidObservationsError("No valid observations found.")
    latest_end = pc.call_function("max", [valid_rows["End"]])
    latest_mask = pc.call_function("equal", [valid_rows["End"], latest_end])
    latest_rows = valid_rows.filter(latest_mask)

    if latest_rows.num_rows != 1:
        raise ValueError("Latest observation timestamp is not unique.")

    py_table = latest_rows.to_pylist()
    latest_observation = py_table[0]

    return latest_observation


def select_valid_history(table: pa.Table) -> list[dict]:
    validity_mask = pc.call_function(
        "is_in",
        [table["Validity"]],
        options=pc.SetLookupOptions(value_set=pa.array([1, 2, 3, 4])),
    )
    valid_rows = table.filter(validity_mask)
    if valid_rows.num_rows == 0:
        raise NoValidObservationsError("No valid observations found.")
    rows = valid_rows.to_pylist()
    return rows


def fetch_parquet_table(parquet_url: str) -> pa.Table:
    parse_series_url(parquet_url)
    request = urllib.request.Request(parquet_url, method="GET")
    for attempt in range(3):
        try:
            parquet_bytes = read_response(request, MAX_PARQUET_BYTES)
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            delay = min(int(retry_after), 30) if retry_after.isdigit() else 2**attempt
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    else:
        raise RuntimeError("Download retry limit exhausted.")
    return decode_parquet_table(parquet_bytes)


def decode_parquet_table(parquet_bytes: bytes) -> pa.Table:
    parquet = pq.ParquetFile(
        pa.BufferReader(parquet_bytes),
        thrift_string_size_limit=1024 * 1024,
        thrift_container_size_limit=10_000,
    )
    if parquet.metadata.num_columns > 32 or parquet.metadata.num_row_groups > 1024:
        raise ValueError("Parquet schema/group limit exceeded.")
    if parquet.metadata.num_rows > MAX_PARQUET_ROWS:
        raise ValueError("Parquet row limit exceeded.")
    if (
        sum(
            parquet.metadata.row_group(i).total_byte_size
            for i in range(parquet.metadata.num_row_groups)
        )
        > MAX_DECODED_BYTES
    ):
        raise ValueError("Parquet decoded-size limit exceeded.")
    if any(pa.types.is_nested(field.type) for field in parquet.schema_arrow):
        raise ValueError("Nested Parquet fields are not supported.")
    table = parquet.read()
    if table.nbytes > MAX_DECODED_BYTES:
        raise ValueError("Parquet decoded-size limit exceeded.")
    return table


def fetch_latest_observation(parquet_url: str) -> dict:
    table = fetch_parquet_table(parquet_url)
    latest_observation = select_latest_valid_observation(table)
    return latest_observation


def fetch_valid_history(parquet_url: str) -> list[dict]:
    table = fetch_parquet_table(parquet_url)
    valid_history = select_valid_history(table)
    return valid_history


def extract_station_id(sampling_point_id: str) -> str:
    if not isinstance(sampling_point_id, str):
        raise TypeError("Sampling point must be text.")
    sampling_point_parts = sampling_point_id.split("_")

    if len(sampling_point_parts) != 3:
        raise ValueError("Sampling Point Error.")
    station_part = sampling_point_parts[0]

    if not station_part.startswith("RO/SPO-"):
        raise ValueError("Invalid station.")

    station_id = station_part.removeprefix("RO/SPO-")

    if not STATION_PATTERN.fullmatch(station_id):
        raise ValueError("Station Id not valid.")

    if (
        not re.fullmatch(r"[0-9]{5}", sampling_point_parts[1])
        or not sampling_point_parts[2].isdigit()
    ):
        raise ValueError("Invalid sampling point pollutant or sequence.")
    return station_id


def fetch_dataflow_d_station_metadata(station_id: str) -> dict:
    init_url = "https://discomap.eea.europa.eu/App/AQViewer/init?fqn=Airquality_Dissem.b2g.Measurements"
    init_request = urllib.request.Request(
        init_url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://discomap.eea.europa.eu/App/AQViewer/index.html?fqn=Airquality_Dissem.b2g.Measurements",
        },
        method="GET",
    )

    init_body = read_response(init_request, 8 * 1024 * 1024)
    init_payload = json.loads(init_body.decode("utf-8"))
    if not isinstance(init_payload, dict):
        raise TypeError(
            f"Invalid Dataflow D init response for {station_id}: expected an object."
        )
    if not isinstance(init_payload.get("Request"), dict):
        raise TypeError(
            f"Invalid Dataflow D init response for {station_id}: missing Request object."
        )
    if not isinstance(init_payload["Request"].get("RequestFilter"), dict):
        raise TypeError(
            f"Invalid Dataflow D init response for {station_id}: missing RequestFilter object."
        )
    init_payload["Request"]["RequestFilter"]["AirQualityStationEoICode"] = {
        "FieldName": "AirQualityStationEoICode",
        "Values": [station_id],
    }

    filter_url = "https://discomap.eea.europa.eu/App/AQViewer/filter?fqn=Airquality_Dissem.b2g.Measurements"
    filter_request_body = json.dumps(init_payload["Request"]).encode("utf-8")
    filter_request = urllib.request.Request(
        filter_url,
        data=filter_request_body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://discomap.eea.europa.eu/App/AQViewer/index.html?fqn=Airquality_Dissem.b2g.Measurements",
            "Origin": "https://discomap.eea.europa.eu",
        },
        method="POST",
    )
    filter_response_body = read_response(filter_request, 8 * 1024 * 1024)
    filter_payload = json.loads(filter_response_body.decode("utf-8"))
    if not isinstance(filter_payload, dict):
        raise TypeError(
            f"Invalid Dataflow D filter response for {station_id}: expected an object."
        )
    if not isinstance(filter_payload.get("Preview"), dict):
        raise TypeError(
            f"Invalid Dataflow D filter response for {station_id}: missing Preview object."
        )
    if not isinstance(filter_payload["Preview"].get("Rows"), list):
        raise TypeError(
            f"Invalid Dataflow D filter response for {station_id}: missing Rows list."
        )
    if type(filter_payload["Preview"].get("TotalRows")) is not int:
        raise TypeError(
            f"Invalid Dataflow D filter response for {station_id}: missing TotalRows integer."
        )
    if len(filter_payload["Preview"]["Rows"]) != filter_payload["Preview"]["TotalRows"]:
        raise ValueError(
            f"Incomplete Dataflow D results for {station_id}: received "
            f"{len(filter_payload['Preview']['Rows'])} of "
            f"{filter_payload['Preview'].get('TotalRows')} rows."
        )
    if len(filter_payload["Preview"]["Rows"]) == 0:
        raise ValueError(f"No Dataflow D rows found for {station_id}.")
    station_variants = set()
    for row in filter_payload["Preview"]["Rows"]:
        if not isinstance(row, dict):
            raise TypeError(
                f"Invalid Dataflow D row for {station_id}: expected an object."
            )
        if row.get("AirQualityStationEoICode") != station_id:
            raise ValueError(f"Dataflow D row station ID does not match {station_id}.")
        if row.get("Country") != "Romania":
            raise ValueError(f"Dataflow D row for {station_id} is not from Romania.")
        if not isinstance(row.get("AQStationName"), str):
            raise TypeError(
                f"Invalid Dataflow D station name for {station_id}: expected text."
            )
        if row["AQStationName"].strip() == "":
            raise ValueError(
                f"Invalid Dataflow D station name for {station_id}: blank text."
            )
        if type(row.get("Longitude")) not in (int, float):
            raise TypeError(
                f"Invalid Dataflow D longitude for {station_id}: expected a number."
            )
        if not (-180 <= row["Longitude"] <= 180):
            raise ValueError(
                f"Invalid Dataflow D longitude for {station_id}: outside -180 to 180."
            )
        if type(row.get("Latitude")) not in (int, float):
            raise TypeError(
                f"Invalid Dataflow D latitude for {station_id}: expected a number."
            )
        if not (-90 <= row["Latitude"] <= 90):
            raise ValueError(
                f"Invalid Dataflow D latitude for {station_id}: outside -90 to 90."
            )
        station_variants.add((row["AQStationName"], row["Longitude"], row["Latitude"]))
    if len(station_variants) != 1:
        raise ValueError(
            f"Conflicting Dataflow D station metadata for {station_id}: "
            f"{len(station_variants)} variants."
        )
    station_name, longitude, latitude = station_variants.pop()
    return {
        "stationId": station_id,
        "stationName": station_name,
        "longitude": longitude,
        "latitude": latitude,
    }


def fetch_station_metadata(station_id: str) -> dict:
    metadata_params = {
        "where": f"AirQualityStationEoICode='{station_id}'",
        "outFields": "AirQualityStationEoICode,AQStationName",
        "returnGeometry": "true",
        "outSR": 4326,
        "f": "geojson",
    }

    metadata_query = urllib.parse.urlencode(metadata_params)

    metadata_base_url = "https://air.discomap.eea.europa.eu/arcgis/rest/services/AirQuality/AirQualityDownloadServiceEUMonitoringStations/MapServer/0/query"
    metadata_url = f"{metadata_base_url}?{metadata_query}"

    metadata_request = urllib.request.Request(metadata_url, method="GET")

    try:
        metadata_body = read_response(metadata_request, 8 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        if 500 <= error.code < 600:
            return fetch_dataflow_d_station_metadata(station_id)
        raise
    except (urllib.error.URLError, TimeoutError):
        return fetch_dataflow_d_station_metadata(station_id)

    metadata = json.loads(metadata_body.decode("utf-8"))

    if not isinstance(metadata, dict) or metadata.get("type") != "FeatureCollection":
        raise ValueError

    features = metadata.get("features")
    if not isinstance(features, list):
        raise TypeError(f"Invalid metadata features for {station_id}.")
    if len(features) == 0:
        return fetch_dataflow_d_station_metadata(station_id)
    elif len(features) > 1:
        raise ValueError(
            f"Expected one metadata feature for {station_id}, found {len(features)}."
        )

    station_feature = features[0]
    if not isinstance(station_feature, dict):
        raise TypeError("Invalid ArcGIS station feature.")
    properties = station_feature.get("properties")
    geometry = station_feature.get("geometry")
    if not (isinstance(properties, dict)) or not (isinstance(geometry, dict)):
        raise TypeError

    if properties["AirQualityStationEoICode"] != station_id:
        raise ValueError
    if (
        not (isinstance(properties["AQStationName"], str))
        or len(properties["AQStationName"]) == 0
    ):
        raise ValueError
    if geometry["type"] != "Point":
        raise ValueError
    if (
        not (isinstance(geometry["coordinates"], list))
        or len(geometry["coordinates"]) != 2
    ):
        raise ValueError

    station_name = properties["AQStationName"]
    coordinates = geometry["coordinates"]
    longitude = coordinates[0]
    latitude = coordinates[1]

    result = {
        "stationId": station_id,
        "stationName": station_name,
        "longitude": longitude,
        "latitude": latitude,
    }
    validate_station_metadata(result, station_id)
    return result


def normalize_observation(
    raw_observation: dict, station_metadata: dict, source_url: str
) -> dict:
    validate_raw_identity(raw_observation, source_url)
    validate_station_metadata(
        station_metadata, extract_station_id(raw_observation["Samplingpoint"])
    )
    normalized_value = finite_number(raw_observation["Value"], "Observation value")
    normalized_start = to_eea_iso(raw_observation["Start"])
    normalized_end = to_eea_iso(raw_observation["End"])
    normalized_result_time = to_eea_iso(raw_observation["ResultTime"])
    normalized_ingested_at = datetime.now(UTC).isoformat()

    if raw_observation["Pollutant"] not in POLLUTANT_NAMES:
        raise ValueError("Invalid Pollutant.")

    normalized_pollutant = POLLUTANT_NAMES[raw_observation["Pollutant"]]

    verification_code = raw_observation["Verification"]
    if verification_code == 1:
        normalized_status = "validated"
    elif verification_code == 2 or verification_code == 3:
        normalized_status = "preliminary"
    else:
        raise ValueError("Invalid Verification Code.")

    normalized_measurement = {
        "source": "EEA",
        "aggregationType": raw_observation["AggType"],
        "sourceRecordId": raw_observation.get("FkObservationLog"),
        "dataCapture": (
            finite_number(raw_observation["DataCapture"], "Data capture")
            if raw_observation.get("DataCapture") is not None
            else None
        ),
        "samplingPointId": raw_observation["Samplingpoint"],
        "pollutant": normalized_pollutant,
        "value": normalized_value,
        "unit": raw_observation["Unit"],
        "reportedAt": normalized_result_time,
        "observedFrom": normalized_start,
        "observedTo": normalized_end,
        "ingestedAt": normalized_ingested_at,
        "validity": raw_observation["Validity"],
        "verification": raw_observation["Verification"],
        "sourceUrl": source_url,
        "status": normalized_status,
        "stationId": station_metadata["stationId"],
        "stationName": station_metadata["stationName"],
        "longitude": station_metadata["longitude"],
        "latitude": station_metadata["latitude"],
    }

    validate_observation(normalized_measurement)
    return normalized_measurement


def load_station_metadata_cache(cache_path: Path) -> dict:
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            raise TypeError("Cache is not an object.")
        return cache
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as error:
        print(f"Metadata cache discarded: {type(error).__name__}", file=sys.stderr)
        return {}


def get_station_metadata(station_id: str, cache_path: Path) -> dict:
    cache = load_station_metadata_cache(cache_path)
    entry = cache.get(station_id)
    if isinstance(entry, dict):
        try:
            metadata = entry["metadata"]
            validate_station_metadata(metadata, station_id)
            cached_at = parse_timestamp(entry["fetchedAt"])
            if timedelta(0) <= datetime.now(UTC) - cached_at < METADATA_TTL:
                return metadata
        except (ValueError, TypeError, KeyError):
            pass
    metadata = fetch_station_metadata(station_id)
    validate_station_metadata(metadata, station_id)
    cache[station_id] = {
        "metadata": metadata,
        "fetchedAt": datetime.now(UTC).isoformat(),
    }
    try:
        atomic_write_json(cache, cache_path)
    except (OSError, ValueError, TypeError) as error:
        print(f"Metadata cache write skipped: {type(error).__name__}", file=sys.stderr)
    return metadata


def import_observation(
    parquet_url: str, cache_path: Path = STATION_METADATA_CACHE_PATH
) -> dict:
    raw_observation = fetch_latest_observation(parquet_url)
    station_id = validate_raw_identity(raw_observation, parquet_url)
    station_metadata = get_station_metadata(station_id, cache_path)
    normalized_observation = normalize_observation(
        raw_observation, station_metadata, parquet_url
    )
    return normalized_observation


def import_history(
    parquet_url: str, cache_path: Path = STATION_METADATA_CACHE_PATH
) -> list[dict]:
    raw_history = fetch_valid_history(parquet_url)
    if not raw_history:
        raise NoValidObservationsError("No valid observations found.")
    station_id = validate_raw_identity(raw_history[0], parquet_url)
    for row in raw_history:
        validate_raw_identity(row, parquet_url)
    station_metadata = get_station_metadata(station_id, cache_path)
    return [
        normalize_observation(row, station_metadata, parquet_url) for row in raw_history
    ]


def collect_history(
    parquet_urls: list[str], cache_path: Path = STATION_METADATA_CACHE_PATH
) -> dict:
    observations = []
    summary = dict.fromkeys(
        ("attempted", "imported", "skipped", "failed", "duplicateCount"), 0
    )
    for url in dict.fromkeys(parquet_urls):
        summary["attempted"] += 1
        try:
            parse_series_url(url)
            rows = import_history(url, cache_path)
            if not rows:
                raise NoValidObservationsError("No valid observations found.")
            unique_rows = {}
            duplicates = 0
            for row in rows:
                validate_observation(row)
                if row["sourceUrl"] != url:
                    raise ValueError("History row belongs to another source series.")
                key = (
                    row["source"],
                    row["samplingPointId"],
                    row["aggregationType"],
                    parse_timestamp(row["observedFrom"]),
                    parse_timestamp(row["observedTo"]),
                )
                if key in unique_rows:
                    previous = unique_rows[key]
                    if {k: v for k, v in previous.items() if k != "ingestedAt"} != {
                        k: v for k, v in row.items() if k != "ingestedAt"
                    }:
                        raise ValueError("Conflicting observations for one interval.")
                    duplicates += 1
                else:
                    unique_rows[key] = row
        except NoValidObservationsError:
            summary["skipped"] += 1
            print(f"No valid history found for {url}", file=sys.stderr)
            continue
        except (
            urllib.error.URLError,
            TimeoutError,
            pa.ArrowException,
            ValueError,
            TypeError,
            KeyError,
            OSError,
        ) as error:
            summary["failed"] += 1
            print(f"Error importing history {url}: {error}", file=sys.stderr)
            continue
        observations.extend(unique_rows.values())
        summary["imported"] += 1
        summary["duplicateCount"] += duplicates
    summary["observationCount"] = len(observations)
    return {"observations": observations, "importSummary": summary}


def collect_observations(parquet_urls: list[str]) -> dict:
    normalized_observations = []
    skipped_counter = 0
    failed_counter = 0
    attempted_counter = 0
    grouped_urls = group_series_urls(parquet_urls)
    for group in grouped_urls.values():
        for candidate in group:
            parquet_url = candidate[1]
            try:
                attempted_counter += 1
                normalized_observation = import_observation(parquet_url)
                normalized_observations.append(normalized_observation)
                break
            except NoValidObservationsError:
                skipped_counter += 1
                print(f"No valid observation found for {parquet_url}", file=sys.stderr)
                continue
            except (
                urllib.error.URLError,
                TimeoutError,
                pa.ArrowException,
                ValueError,
                TypeError,
                KeyError,
                OSError,
            ) as error:
                failed_counter += 1
                print(f"Error importing {parquet_url}: {error}", file=sys.stderr)
    assert (
        attempted_counter
        == len(normalized_observations) + skipped_counter + failed_counter
    )
    import_result = {
        "observations": normalized_observations,
        "importSummary": {
            "attempted": attempted_counter,
            "skipped": skipped_counter,
            "failed": failed_counter,
            "imported": len(normalized_observations),
        },
    }
    return import_result


def parse_series_url(parquet_url: str):
    validate_https_url(parquet_url, {PARQUET_HOST})
    parsed = urllib.parse.urlsplit(parquet_url)
    if parsed.query:
        raise ValueError("Parquet URL query parameters are not permitted.")
    match = SERIES_PATTERN.fullmatch(Path(parsed.path).name)
    if not match or parsed.path != f"/airquality-p/RO/{Path(parsed.path).name}":
        raise ValueError("Invalid EEA series filename or path.")
    station_id, pollutant_code, sequence = match.groups()
    if int(pollutant_code) not in POLLUTANT_NAMES:
        raise ValueError("Unsupported EEA pollutant.")
    return station_id, pollutant_code, int(sequence)


def group_series_urls(url_list: list[str]) -> dict:
    grouped_urls = {}
    for url in url_list:
        station_id, pollutant_code, sequence = parse_series_url(url)
        key = (station_id, pollutant_code)
        if key not in grouped_urls:
            grouped_urls[key] = []
        grouped_urls[key].append((sequence, url))
    for value in grouped_urls.values():
        value.sort(reverse=True)
    return grouped_urls


def is_previous_observation_invalidated(
    table: pa.Table, previous_observation: dict
) -> bool:
    previous_start = parse_timestamp(previous_observation["observedFrom"])
    previous_end = parse_timestamp(previous_observation["observedTo"])
    aggregation = previous_observation.get(
        "aggregationType",
        "hour" if previous_end - previous_start == timedelta(hours=1) else "day",
    )
    matches = []
    for row in table.to_pylist():
        if row["Samplingpoint"] != previous_observation["samplingPointId"]:
            continue
        validate_raw_identity(row, previous_observation["sourceUrl"])
        if (
            parse_timestamp(to_eea_iso(row["Start"])) == previous_start
            and parse_timestamp(to_eea_iso(row["End"])) == previous_end
        ):
            if (
                row["AggType"] != aggregation
                or row["Unit"] != previous_observation["unit"]
            ):
                raise ValueError("Previous observation aggregation or unit changed.")
            if type(row["Validity"]) is not int or row["Validity"] not in (
                -99,
                -1,
                1,
                2,
                3,
                4,
            ):
                raise ValueError("Unknown source validity code.")
            matches.append(row)
    if len(matches) > 1:
        raise ValueError("Previous observation interval is not unique in the source.")
    if not matches:
        return False
    return matches[0]["Validity"] == -1


def write_observation_snapshot(import_result: dict, path: Path) -> None:
    validate_document(import_result)
    failed_count = import_result["importSummary"]["failed"]
    if failed_count > 0:
        raise ValueError(
            f"Import has {failed_count} failed attempts; keeping the previous snapshot."
        )
    current = {
        (r["stationId"], r["pollutant"]): r for r in import_result["observations"]
    }
    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        validate_document(previous)
        previous_rows = {
            (r["stationId"], r["pollutant"]): r for r in previous["observations"]
        }
        missing = previous_rows.keys() - current.keys()
        now = datetime.now(UTC)
        recent_missing = []
        for missed in missing:
            previous_observation = previous_rows[missed]
            previous_end = parse_timestamp(previous_observation["observedTo"])
            observation_age = now - previous_end
            is_recent = observation_age < timedelta(hours=6)
            if is_recent:
                recent_missing.append(missed)
        if recent_missing:
            raise ValueError(
                f"Import lost {len(recent_missing)} station/pollutant pairs with observations less than 6 hours old; keeping the previous snapshot."
            )
        regressed = [
            key
            for key in previous_rows & current.keys()
            if parse_timestamp(current[key]["observedTo"])
            < parse_timestamp(previous_rows[key]["observedTo"])
        ]
        unconfirmed = []
        for key in regressed:
            previous_observation = previous_rows[key]
            candidate = current[key]
            if candidate["sourceUrl"] != previous_observation["sourceUrl"]:
                unconfirmed.append(key)
                continue
            table = fetch_parquet_table(previous_observation["sourceUrl"])
            for row in table.to_pylist():
                validate_raw_identity(row, previous_observation["sourceUrl"])
            if not is_previous_observation_invalidated(table, previous_observation):
                unconfirmed.append(key)
                continue
            latest = select_latest_valid_observation(table)
            metadata = {
                field: candidate[field]
                for field in ("stationId", "stationName", "longitude", "latitude")
            }
            verified = normalize_observation(latest, metadata, candidate["sourceUrl"])
            candidate_fields = dict(candidate)
            candidate_fields.setdefault("aggregationType", verified["aggregationType"])
            candidate_fields.setdefault("sourceRecordId", None)
            candidate_fields.setdefault("dataCapture", None)
            times = ("observedFrom", "observedTo", "reportedAt")
            if any(
                (
                    parse_timestamp(verified[field])
                    != parse_timestamp(candidate_fields[field])
                    if field in times
                    else verified[field] != candidate_fields[field]
                )
                for field in verified
                if field != "ingestedAt"
            ):
                unconfirmed.append(key)
                continue
            print(
                f"Confirmed source invalidation for {key[0]}/{key[1]} "
                f"at {previous_observation['observedFrom']} to {previous_observation['observedTo']}"
            )
        if unconfirmed:
            raise ValueError(
                f"Import regressed {len(unconfirmed)} observation intervals without confirmed source invalidation; keeping the previous snapshot. Affected pairs: {unconfirmed}"
            )
    atomic_write_json(import_result, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import or validate the EEA snapshot.")
    parser.add_argument(
        "--check",
        type=Path,
        help="Validate a snapshot without network access or mutation",
    )
    args = parser.parse_args()
    if args.check is not None:
        validate_document(json.loads(args.check.read_text(encoding="utf-8")))
        print("Snapshot contract valid")
        return
    urls = fetch_parquet_urls("RO", list(POLLUTANT_NAMES.values()))
    urls = sorted(urls)
    import_result = collect_observations(urls)
    path = Path(__file__).resolve().parent.parent / "src" / "data" / "observations.json"
    write_observation_snapshot(import_result, path)


if __name__ == "__main__":
    main()
