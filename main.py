"""
Ana v3 — Secretária Virtual de Anestesiologia
PostgreSQL + SQLite, email, Google Calendar, relatórios
"""

from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any, List
import os, logging, hashlib, secrets, json, base64, io
import re, time
from datetime import datetime, timedelta
import urllib.request, urllib.error, urllib.parse

try:
    from pypdf import PdfReader
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
log = logging.getLogger("ana")

app = FastAPI(title="Ana v3", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SECRET         = os.environ.get("SECRET_KEY", "ana-secretaria-default-secret-change-me")
GROQ_KEY       = os.environ.get("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL   = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "A.N.A <onboarding@resend.dev>")
SMTP_HOST      = os.environ.get("SMTP_HOST", "")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER      = os.environ.get("SMTP_USER", "")
SMTP_PASS      = os.environ.get("SMTP_PASS", "")
SMTP_FROM      = os.environ.get("SMTP_FROM", "ana@grupo-anestesia.com")
GCAL_CREDS     = os.environ.get("GCAL_CREDENTIALS", "")
GCAL_ID        = os.environ.get("GCAL_CALENDAR_ID", "primary")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")