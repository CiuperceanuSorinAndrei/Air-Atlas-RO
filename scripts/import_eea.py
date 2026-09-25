import json
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

POLLUTANT_NAMES = {1: "SO2", 5: "PM10", 7: "O3", 8: "NO2", 6001: "PM2.5"}
EEA_TIMEZONE = timezone(timedelta(hours=1))
STATION_METADATA_CACHE_PATH = (
    Path(__file__).resolve().parent.parent / ".cache" / "eea_station_metadata.json"
)


class NoValidObservationsError(ValueError):
    pass


def to_eea_iso(value):
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
            with urllib.request.urlopen(request, timeout=30) as response:
                response_body = response.read()
            break
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt == 2 or (
                isinstance(error, urllib.error.HTTPError) and error.code < 500
            ):
                raise
            time.sleep(2**attempt)

    response_text = response_body.decode("utf-8-sig")
    lines = response_text.splitlines()
    urls = []

    if not lines or lines[0] != "ParquetFileUrl":
        raise ValueError("Unexpected EEA response format.")

    for line in lines[1:]:
        line = line.strip()
        if line:
            urls.append(line)

    if not urls:
        raise ValueError("Urls are not available.")

    return urls


def select_latest_valid_observation(table: pa.Table) -> dict:
    validity_mask = pc.is_in(table["Validity"], value_set=pa.array([1, 2, 3, 4]))
    valid_rows = table.filter(validity_mask)
    if valid_rows.num_rows == 0:
        raise NoValidObservationsError("No valid observations found.")
    latest_end = pc.max(valid_rows["End"])
    latest_mask = pc.equal(valid_rows["End"], latest_end)
    latest_rows = valid_rows.filter(latest_mask)

    if latest_rows.num_rows != 1:
        raise ValueError("Latest observation timestamp is not unique.")

    py_table = latest_rows.to_pylist()
    latest_observation = py_table[0]

    return latest_observation


def fetch_latest_observation(parquet_url: str) -> dict:
    sample_request = urllib.request.Request(parquet_url, method="GET")

    with urllib.request.urlopen(sample_request, timeout=30) as response:
        parquet_bytes = response.read()

    reader = pa.BufferReader(parquet_bytes)
    table = pq.read_table(reader)
    latest_observation = select_latest_valid_observation(table)
    return latest_observation


def extract_station_id(sampling_point_id: str) -> str:
    sampling_point_parts = sampling_point_id.split("_")

    if len(sampling_point_parts) != 3:
        raise ValueError("Sampling Point Error.")
    station_part = sampling_point_parts[0]

    if not station_part.startswith("RO/SPO-"):
        raise ValueError("Invalid station.")

    station_id = station_part.removeprefix("RO/SPO-")

    if not (
        len(station_id) <= 8 and station_id.startswith("RO") and station_id.isalnum()
    ):
        raise ValueError("Station Id not valid.")

    return station_id


def fetch_dataflow_d_station_metadata(station_id: str) -> dict:
    init_url = "https://discomap.eea.europa.eu/App/AQViewer/init?fqn=Airquality_Dissem.b2g.Measurements"
    init_request = urllib.request.Request(init_url, method="GET")

    with urllib.request.urlopen(init_request, timeout=30) as response:
        init_body = response.read()
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
    with urllib.request.urlopen(filter_request, timeout=30) as response:
        filter_response_body = response.read()
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
        with urllib.request.urlopen(metadata_request, timeout=30) as response:
            metadata_body = response.read()
    except urllib.error.HTTPError as error:
        if 500 <= error.code < 600:
            return fetch_dataflow_d_station_metadata(station_id)
        raise
    except (urllib.error.URLError, TimeoutError):
        return fetch_dataflow_d_station_metadata(station_id)

    metadata = json.loads(metadata_body.decode("utf-8"))

    if metadata.get("type") != "FeatureCollection":
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

    if not (isinstance(latitude, int)) and not (isinstance(latitude, float)):
        raise TypeError
    if not (isinstance(longitude, int)) and not (isinstance(longitude, float)):
        raise TypeError

    if longitude > 180 or longitude < -180:
        raise ValueError
    if latitude > 90 or latitude < -90:
        raise ValueError
    return {
        "stationId": station_id,
        "stationName": station_name,
        "longitude": longitude,
        "latitude": latitude,
    }


def normalize_observation(
    raw_observation: dict, station_metadata: dict, source_url: str
) -> dict:
    normalized_value = float(raw_observation["Value"])
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

    return normalized_measurement


def load_station_metadata_cache(cache_path: Path) -> dict:
    if not cache_path.exists():
        return {}
    cache = cache_path.read_text(encoding="utf-8")
    cache_dict = json.loads(cache)
    return cache_dict


def get_station_metadata(station_id: str, cache_path: Path) -> dict:
    cache_dict = load_station_metadata_cache(cache_path)
    if station_id in cache_dict:
        return cache_dict[station_id]
    station_metadata = fetch_station_metadata(station_id)
    cache_dict[station_id] = station_metadata
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_json = json.dumps(cache_dict, ensure_ascii=False, indent=2)
    cache_path.write_text(cache_json + "\n", encoding="utf-8")
    return station_metadata


def import_observation(
    parquet_url: str, cache_path: Path = STATION_METADATA_CACHE_PATH
) -> dict:
    raw_observation = fetch_latest_observation(parquet_url)
    sampling_point_id = raw_observation["Samplingpoint"]
    station_id = extract_station_id(sampling_point_id)
    station_metadata = get_station_metadata(station_id, cache_path)
    normalized_observation = normalize_observation(
        raw_observation, station_metadata, parquet_url
    )
    return normalized_observation


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
    parsed_url = urllib.parse.urlparse(parquet_url)
    path = Path(parsed_url.path)
    splitted = path.stem.split("_")
    if len(splitted) != 3:
        raise ValueError("Invalid EEA series filename.")
    if not (splitted[0].startswith("SPO-")):
        raise ValueError("Invalid EEA series filename.")
    station_id = splitted[0].removeprefix("SPO-")
    pollutant_code = splitted[1]
    raw_sequence = splitted[2]
    sequence = int(raw_sequence)
    return station_id, pollutant_code, sequence


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


def write_observation_snapshot(import_result: dict, path: Path) -> None:
    observations = import_result["observations"]
    if not observations:
        raise ValueError(
            "Import produced no observations; keeping the previous snapshot."
        )

    current_pairs = {
        (observation["stationId"], observation["pollutant"])
        for observation in observations
    }
    if len(current_pairs) != len(observations):
        raise ValueError("Import contains duplicate station/pollutant pairs.")
    if import_result["importSummary"]["imported"] != len(observations):
        raise ValueError("Import summary does not match observations.")

    if path.exists():
        previous = json.loads(path.read_text(encoding="utf-8"))
        previous_pairs = {
            (observation["stationId"], observation["pollutant"])
            for observation in previous["observations"]
        }
        missing_pairs = previous_pairs - current_pairs
        if missing_pairs:
            raise ValueError(
                f"Import lost {len(missing_pairs)} existing station/pollutant pairs; "
                "keeping the previous snapshot."
            )

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
            json.dump(import_result, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    urls = fetch_parquet_urls("RO", ["NO2", "PM10"])
    urls = sorted(urls)
    import_result = collect_observations(urls)
    path = Path(__file__).resolve().parent.parent / "src" / "data" / "observations.json"
    write_observation_snapshot(import_result, path)


if __name__ == "__main__":
    main()
