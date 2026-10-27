"""Run on your own computer with a browser, never on the public server."""
import argparse
import os
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

parser = argparse.ArgumentParser()
parser.add_argument("client_json")
parser.add_argument("--output", default="token.json")
args = parser.parse_args()
os.umask(0o077)
output = Path(args.output)
if output.exists():
    parser.error("Output already exists; use another path to avoid overwriting credentials")
flow = InstalledAppFlow.from_client_secrets_file(args.client_json, ["https://www.googleapis.com/auth/drive.file"])
credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
service = build("drive", "v3", credentials=credentials)
folder = service.files().create(body={"name": "Telegram media", "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
with output.open("x") as f:
    f.write(credentials.to_json())
print("root_folder_id:", folder["id"])
print("Credentials saved; transfer securely, never commit them.")
