import json
import sys
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

    with urllib.request.urlopen(request, timeout=30) as response:
        response_body = response.read()

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

    with urllib.request.urlopen(metadata_request, timeout=30) as response:
        metadata_body = response.read()

    metadata = json.loads(metadata_body.decode("utf-8"))

    if metadata.get("type") != "FeatureCollection":
        raise ValueError

    features = metadata.get("features")
    if not (isinstance(features, list)) or len(features) != 1:
        raise ValueError

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


def collect_observations(selected_urls: list[str]) -> dict:
    normalized_observations = []
    skipped_counter = 0
    failed_counter = 0
    for parquet_url in selected_urls:
        try:
            raw_observation = fetch_latest_observation(parquet_url)
            sampling_point_id = raw_observation["Samplingpoint"]
            station_id = extract_station_id(sampling_point_id)
            station_metadata = fetch_station_metadata(station_id)
            normalized_observation = normalize_observation(
                raw_observation, station_metadata, parquet_url
            )
            normalized_observations.append(normalized_observation)
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
        len(selected_urls)
        == len(normalized_observations) + skipped_counter + failed_counter
    )
    import_result = {
        "observations": normalized_observations,
        "importSummary": {
            "attempted": len(selected_urls),
            "skipped": skipped_counter,
            "failed": failed_counter,
            "imported": len(normalized_observations),
        },
    }
    return import_result


def main() -> None:
    urls = fetch_parquet_urls("RO", ["NO2", "PM10"])
    urls = sorted(urls)
    selected_urls = urls[:5]
    import_result = collect_observations(selected_urls)
    output = json.dumps(import_result, ensure_ascii=False, indent=2)
    path = Path(__file__).resolve().parent.parent / "src" / "data" / "observations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
