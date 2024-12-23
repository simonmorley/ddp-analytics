import requests
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
import os
import base64
import json

# Elasticsearch connection details
ES_HOST = os.getenv("ES_HOST", "http://localhost:9202")
ES_INDEX = "analytics"

# Google Sheets details
SHEET_NAME = "1HJ4nN_h02PQuSfKtMkWX7R8IMV3mjcWeTpBoFFblSYI"  # Replace with your Google Sheet name

base64_creds = os.getenv("BASE64_ENCODED_CREDENTIALS")

if not base64_creds:
    raise ValueError("BASE64_ENCODED_CREDENTIALS environment variable is not set.")

# Decode and parse the credentials JSON
credentials_info = base64.b64decode(base64_creds).decode("utf-8")

def get_last_insert_date(sheet):
    """
    Fetch the last inserted timestamp from Google Sheets.
    Assumes the timestamp (in Unix epoch format) is in the specified column.
    """
    # Get all values in the specified column (e.g., column 6 for `timestamp`)
    column_values = sheet.col_values(1)  # Replace with the correct column index if needed

    # Ignore the header and fetch the last non-empty row
    if len(column_values) > 1:
        last_value = column_values[-1]  # Get the last value in the column
        try:
            # Convert to integer and return the Unix timestamp
            return int(last_value)
        except ValueError:
            raise ValueError(f"Unable to parse the timestamp from Google Sheets: {last_value}")

    return None  # Return None if no valid timestamp is found

def fetch_es_data(last_timestamp=None):
    """Fetch all new data from Elasticsearch with pagination."""
    # Define fields to extract
    fields_to_keep = [
        "user_agent", "client_id", "ip_address", "accept_language",
        "referer", "timestamp", "event_type", "element_id",
        "location", "category"
    ]

    # Elasticsearch query template
    base_query = {
        "query": {
            "bool": {
                "must": [
                    {
                        "term": {
                            "event_details.event_type": {
                                "value": "search"
                            }
                        }
                    }
                ],
                "filter": [
                    {
                        "range": {
                            "metadata.timestamp": {
                                "gt": last_timestamp,
                                "format": "epoch_second"
                            }
                        }
                    }
                ]
            }
        },
        "sort": [
            {
                "metadata.timestamp": {
                    "order": "asc"
                }
            }
        ],
        "size": 10
    }
    
    url = f"{ES_HOST}/{ES_INDEX}/_search"
    batch_size = 100  # Number of records per batch
    from_offset = 0   # Starting offset for pagination
    all_records = []  # Store all fetched records

    while True:
        # Update query for the current batch
        query = base_query.copy()
        query["size"] = batch_size
        query["from"] = from_offset

        # Fetch current batch
        response = requests.post(url, json=query)
        response.raise_for_status()
        data = response.json()

        # Extract and process hits
        hits = data['hits']['hits']
        if not hits:
            break  # Stop if no more records

        for hit in hits:
            source = hit['_source']
            flat_record = {
                **source.get('metadata', {}),
                **source.get('event_details', {}),
                **source.get('raw_data', {}),
            }
            # Keep only specified fields
            filtered_record = {key: flat_record.get(key) for key in fields_to_keep}
            all_records.append(filtered_record)

        # Increment offset for next batch
        from_offset += batch_size

    return all_records

def ensure_headers(sheet, headers):
    """
    Ensure the Google Sheet has the correct headers.
    If the sheet is empty, add the headers.
    If headers already exist, validate them.
    """
    existing_data = sheet.get_all_values()

    if len(existing_data) == 1:
        sheet.append_row(headers, value_input_option="RAW")
        print("Headers added to Google Sheet.")
    else:
        # Validate existing headers
        existing_headers = existing_data[0]
        if existing_headers != headers:
            raise ValueError(
                f"The existing headers in the Google Sheet do not match the expected headers.\n"
                f"Expected: {headers}\n"
                f"Found: {existing_headers}"
            )


def export_to_google_sheets(dataframe, sheet):
    """Append DataFrame rows to Google Sheets."""
    # Define the headers for the Google Sheet
    headers = [
        "timestamp", "converted_date", "user_agent", "client_id",
        "ip_address", "accept_language", "referer", "event_type",
        "element_id", "location", "category"
    ]

    # Ensure headers are present in the sheet
    ensure_headers(sheet, headers)

    # Add a human-readable date column while keeping the original timestamp
    if 'timestamp' in dataframe.columns:
        dataframe['converted_date'] = pd.to_datetime(dataframe['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S')

    # Reorder DataFrame columns to match the headers
    for column in headers:
        if column not in dataframe.columns:
            dataframe[column] = None  # Add missing columns if any
    dataframe = dataframe[headers]

    # Convert DataFrame to a list of lists (Google Sheets format)
    rows = dataframe.values.tolist()

    # Append rows to the sheet
    sheet.append_rows(rows, value_input_option="RAW")
    print(f"Exported {len(rows)} rows to Google Sheets.")


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",  # Full access to Google Sheets
    "https://www.googleapis.com/auth/drive.file"    # Optional: To create/manage specific Drive files
]

def main():
    # Authenticate with Google Sheets
    credentials = Credentials.from_service_account_info(
        json.loads(credentials_info),
        scopes=SCOPES
    )
    gc = gspread.authorize(credentials)

    print(credentials.service_account_email)

    # Open the Google Sheet
    sheet = gc.open_by_key(SHEET_NAME).sheet1

    # # Get the last insert date from Google Sheets
    last_timestamp = get_last_insert_date(sheet)

    if last_timestamp is None:
        last_timestamp = int((pd.Timestamp.now() - pd.DateOffset(months=1)).timestamp())
        
    print(last_timestamp)

    records = fetch_es_data(last_timestamp)
    if not records:
        print("No new records to export.")
        return
    
    # # Convert records to DataFrame
    df = pd.DataFrame(records)

    export_to_google_sheets(df, sheet)
    print("Data exported successfully.")

if __name__ == "__main__":
    main()
