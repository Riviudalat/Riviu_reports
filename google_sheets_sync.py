import json
import os
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CLIENT_SECRET_FILENAME = "google_oauth_client.json"
TOKEN_FILENAME = "google_oauth_token.json"
RESULT_SHEET_PREFIX = "Report Seeding Tiktok"


def rgb(red, green, blue):
    return {"red": red / 255, "green": green / 255, "blue": blue / 255}


def column_widths(column_count):
    widths = [46, 94, 510, 150, 120, 68, 150, 145, 112]
    partner_count = max(0, column_count - 10)
    widths.extend([210] * partner_count)
    widths.append(145)
    return widths[:column_count]


def format_result_sheet(service, spreadsheet_id, sheet_id, row_count, column_count):
    if not row_count or not column_count:
        return

    frozen_rows = 1
    widths = column_widths(column_count)
    requests = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": frozen_rows},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": column_count},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": rgb(208, 226, 243),
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                        "textFormat": {
                            "fontFamily": "Times New Roman",
                            "fontSize": 15,
                            "bold": True,
                            "foregroundColor": rgb(0, 0, 0),
                        },
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,verticalAlignment,wrapStrategy,textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": column_count},
                "cell": {
                    "userEnteredFormat": {
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                        "textFormat": {"fontFamily": "Arial", "fontSize": 10},
                    }
                },
                "fields": "userEnteredFormat(verticalAlignment,wrapStrategy,textFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": 2},
                "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 4, "endColumnIndex": 9},
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "RIGHT",
                        "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                    }
                },
                "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 9, "endColumnIndex": column_count},
                "cell": {"userEnteredFormat": {"horizontalAlignment": "LEFT"}},
                "fields": "userEnteredFormat.horizontalAlignment",
            }
        },
        {
            "updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": column_count},
                "top": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
                "bottom": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
                "left": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
                "right": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
                "innerHorizontal": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
                "innerVertical": {"style": "SOLID", "width": 1, "color": rgb(0, 0, 0)},
            }
        },
    ]

    for index, width in enumerate(widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": index, "endIndex": index + 1},
                "properties": {"pixelSize": width},
                "fields": "pixelSize",
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def client_secret_candidates(base_dir):
    return [
        os.path.join(base_dir, CLIENT_SECRET_FILENAME),
        os.path.join(base_dir, "data", CLIENT_SECRET_FILENAME),
    ]


def client_secret_path(base_dir):
    for path in client_secret_candidates(base_dir):
        if os.path.exists(path):
            return path
    return client_secret_candidates(base_dir)[0]


def token_path(base_dir):
    return os.path.join(base_dir, "data", TOKEN_FILENAME)


def oauth_status(base_dir):
    return {
        "configured": os.path.exists(client_secret_path(base_dir)),
        "authorized": os.path.exists(token_path(base_dir)),
    }


def save_oauth_client(base_dir, payload):
    path = client_secret_path(base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
    return path


def load_credentials(base_dir):
    creds = None
    token_file = token_path(base_dir)
    client_file = client_secret_path(base_dir)

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w", encoding="utf-8") as file_obj:
            file_obj.write(creds.to_json())
        return creds

    if not os.path.exists(client_file):
        raise FileNotFoundError("Chưa có file OAuth client Google.")

    flow = InstalledAppFlow.from_client_secrets_file(client_file, SCOPES)
    creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
    with open(token_file, "w", encoding="utf-8") as file_obj:
        file_obj.write(creds.to_json())
    return creds


def authorize_google(base_dir):
    load_credentials(base_dir)
    return True


def sheets_service(base_dir):
    credentials = load_credentials(base_dir)
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def create_result_sheet_title():
    return f"{RESULT_SHEET_PREFIX} {datetime.now().strftime('%d-%m-%Y-%H-%M')}"


def ensure_unique_sheet_title(existing_titles, desired_title):
    title = desired_title[:100]
    if title not in existing_titles:
        return title
    suffix = 2
    base = title[:95]
    while f"{base}-{suffix}" in existing_titles:
        suffix += 1
    return f"{base}-{suffix}"


def push_rows_to_new_sheet(base_dir, spreadsheet_id, rows):
    service = sheets_service(base_dir)
    spreadsheet = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title",
    ).execute()
    existing_titles = [item["properties"]["title"] for item in spreadsheet.get("sheets", [])]
    title = ensure_unique_sheet_title(existing_titles, create_result_sheet_title())

    add_sheet_response = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
    ).execute()
    sheet_id = add_sheet_response["replies"][0]["addSheet"]["properties"]["sheetId"]

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    format_result_sheet(service, spreadsheet_id, sheet_id, len(rows), max(len(row) for row in rows))
    return title
