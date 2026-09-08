#!/usr/bin/env python3
"""
Eenmalig hulpscriptje: haalt je numerieke Instagram User ID op aan de hand van je
access token. Dat ID (NIET de Meta App ID) heb je nodig als IG_USER_ID in .env
en in de GitHub Secrets.

Gebruik:
  IG_ACCESS_TOKEN=... python get_ig_user_id.py
"""

import os
import sys
import requests

token = os.environ.get("IG_ACCESS_TOKEN")
if not token:
    sys.exit("Zet eerst IG_ACCESS_TOKEN, bv.: IG_ACCESS_TOKEN=xxx python get_ig_user_id.py")

resp = requests.get(
    "https://graph.instagram.com/me",
    params={"fields": "user_id,username", "access_token": token},
    timeout=30,
)
resp.raise_for_status()
data = resp.json()
print(f"Username: {data.get('username')}")
print(f"IG_USER_ID: {data.get('user_id')}")
