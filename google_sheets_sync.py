import json
import os
import urllib.request
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from workbook_utils import format_excel_sheet_datetime

CLIENT_SECRET_FILENAME = "google_oauth_client.json"
TOKEN_FILENAME = "google_oauth_token.json"
RESULT_SHEET_PREFIX = "Report Seeding Tiktok"


def fetch_account_email(access_token):
    token = str(access_token or "").strip()
    if not token:
        return ""
    request = urllib.request.Request(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ""
    return str(payload.get("email") or "").strip()


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def token_has_required_scopes(token_data):
    token_scopes = set(token_data.get("scopes") or [])
    return set(SCOPES).issubset(token_scopes)


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

    if row_count > 2:
        requests.extend([
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_count - 1,
                        "endRowIndex": row_count,
                        "startColumnIndex": 0,
                        "endColumnIndex": column_count,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": rgb(232, 240, 254),
                            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True},
                            "verticalAlignment": "MIDDLE",
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_count - 1,
                        "endRowIndex": row_count,
                        "startColumnIndex": 4,
                        "endColumnIndex": 9,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "RIGHT",
                            "numberFormat": {"type": "NUMBER", "pattern": "#,##0"},
                        }
                    },
                    "fields": "userEnteredFormat(horizontalAlignment,numberFormat)",
                }
            },
        ])

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


def apply_link_formatting(service, spreadsheet_id, sheet_id, rows):
    if not rows or len(rows) < 2:
        return

    requests = []
    for row_index, row in enumerate(rows[1:], start=1):
        url = str(row[2] or "").strip() if len(row) > 2 else ""
        if not url.lower().startswith("http"):
            continue
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": row_index,
                    "endRowIndex": row_index + 1,
                    "startColumnIndex": 2,
                    "endColumnIndex": 3,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "foregroundColor": rgb(0, 74, 198),
                            "underline": True,
                            "link": {"uri": url},
                        }
                    }
                },
                "fields": "userEnteredFormat.textFormat",
            }
        })

    if requests:
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
    token_file = token_path(base_dir)
    account_email = ""
    if os.path.exists(token_file):
        try:
            with open(token_file, "r", encoding="utf-8") as file_obj:
                token_data = json.load(file_obj)
            account_email = str(token_data.get("account") or token_data.get("email") or "").strip()
            if not account_email:
                account_email = fetch_account_email(token_data.get("token", ""))
                if account_email:
                    token_data["account"] = account_email
                    with open(token_file, "w", encoding="utf-8") as file_obj:
                        json.dump(token_data, file_obj, ensure_ascii=False, indent=2)
        except (OSError, json.JSONDecodeError):
            account_email = ""
    return {
        "configured": os.path.exists(client_secret_path(base_dir)),
        "authorized": os.path.exists(token_file),
        "accountEmail": account_email,
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
        token_data = json.loads(open(token_file, "r", encoding="utf-8").read())
        if token_has_required_scopes(token_data):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token and creds.has_scopes(SCOPES):
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
    return format_excel_sheet_datetime()


def ensure_unique_sheet_title(existing_titles, desired_title):
    title = desired_title[:100]
    if title not in existing_titles:
        return title
    suffix = 2
    base = title[:95]
    while f"{base}-{suffix}" in existing_titles:
        suffix += 1
    return f"{base}-{suffix}"


def list_google_sheet_titles(base_dir, spreadsheet_id):
    service = sheets_service(base_dir)
    spreadsheet = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties.title",
    ).execute()
    return [item["properties"]["title"] for item in spreadsheet.get("sheets", [])]


def push_rows_to_sheet(base_dir, spreadsheet_id, rows, sheet_title=""):
    service = sheets_service(base_dir)
    existing_titles = list_google_sheet_titles(base_dir, spreadsheet_id)
    requested_title = str(sheet_title or "").strip()
    title = requested_title if requested_title in existing_titles else ensure_unique_sheet_title(existing_titles, create_result_sheet_title())

    if title not in existing_titles:
        add_sheet_response = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ).execute()
        sheet_id = add_sheet_response["replies"][0]["addSheet"]["properties"]["sheetId"]
    else:
        spreadsheet = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(title,sheetId)",
        ).execute()
        sheet_id = next(
            item["properties"]["sheetId"]
            for item in spreadsheet.get("sheets", [])
            if item["properties"]["title"] == title
        )
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A:ZZ",
            body={},
        ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    format_result_sheet(service, spreadsheet_id, sheet_id, len(rows), max(len(row) for row in rows))
    apply_link_formatting(service, spreadsheet_id, sheet_id, rows)
    return title


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
    apply_link_formatting(service, spreadsheet_id, sheet_id, rows)
    return title
