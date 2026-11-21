"""Diagnóstico R2 anónimo de solo lectura; no envía claves ni crea objetos."""
import json
import logging
from pathlib import Path
import re
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET

logging.disable(logging.CRITICAL)
project = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(project))
from dotenv import dotenv_values
import httpx

values = dotenv_values(project / '.env', interpolate=False)
endpoint, bucket = values.get('R2_ENDPOINT_URL', ''), values.get('R2_BUCKET', '')
report = {'writes': False, 'credentials_sent': False}
try:
    if not re.fullmatch(r'https://[0-9a-f]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com/?', endpoint):
        raise ValueError('Invalid endpoint')
    # El prefijo pertenece a la prueba ya terminada; este GET no crea nada.
    key = 'mitbol-r2-smoke/7da3567d8875475a9f85a94855c64932/privacy-read-only.png'
    url = endpoint.rstrip('/') + '/' + quote(bucket, safe='') + '/' + quote(key, safe='/')
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=15) as client:
        response = client.get(url)
    report['http_status'] = response.status_code
    report['image_returned'] = response.status_code == 200 and response.headers.get('content-type', '').startswith('image/')
    try:
        root = ET.fromstring(response.content[:16384])
        code = root.findtext('Code')
        if code in {'InvalidArgument', 'InvalidRequest', 'Unauthorized', 'AccessDenied', 'NoSuchKey'}:
            report['error_code'] = code
        message = root.findtext('Message') or ''
        argument = root.findtext('ArgumentName') or ''
        report['error_mentions_authorization'] = 'authorization' in (message + ' ' + argument).lower()
    except ET.ParseError:
        report['xml_error_available'] = False
except Exception as error:
    report['error_type'] = type(error).__name__
print(json.dumps(report, ensure_ascii=True, sort_keys=True))
