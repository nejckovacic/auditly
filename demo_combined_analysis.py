# demo_combined_analysis.py
import os
import sys
import json
import base64
import argparse
import requests
import re
import time
from io import BytesIO
from PIL import Image
from bs4 import BeautifulSoup, Comment
from openai import OpenAI

# Utility: split list into batches
def batch_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

# Generate thumbnail for image analysis
def create_thumbnail(path, size=(500,500), max_bytes=200*1024):
    img = Image.open(path).convert('RGB')
    img.thumbnail(size)
    buf = BytesIO(); img.save(buf, format='JPEG', quality=70)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return b64[:max_bytes]

# Load checklist items
def load_checklist(path):
    with open(path, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

# Fetch and split HTML into text chunks
def fetch_and_split_html(url, chunk_size=2000):
    r = requests.get(url, headers={'User-Agent':'AuditlyBot/1.0'}); r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script','style','noscript','iframe']): tag.decompose()
    for c in soup.find_all(string=lambda t:isinstance(t,Comment)): c.extract()
    txt = soup.body.prettify() if soup.body else soup.prettify()
    return [txt[i:i+chunk_size] for i in range(0, len(txt), chunk_size)]

# Analyze image in batches of checklist items
def analyze_image_batches(thumbnail, checklist, client, model, max_tokens):
    issues = []
    sys_prompt = (
        'You are a UX auditor. Given a product page screenshot (Base64) and a list of up to 10 checklist items, '
        'list missing or suboptimal elements in JSON: [{"issue","recommendation","selector","confidence"}].'
    )
    for batch in batch_list(checklist, 10):
        payload = json.dumps({'screenshot': thumbnail, 'checklist': batch})
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role':'system','content':sys_prompt}, {'role':'user','content':payload}],
            temperature=0, max_tokens=max_tokens
        )
        text = resp.choices[0].message.content
        match = re.search(r'(\[.*?\])', text, re.S)
        if match:
            try:
                issues.extend(json.loads(match.group(1)))
            except:
                pass
        time.sleep(1)
    return issues

# Verify issues in code batches of up to 10 issues at a time
def analyze_code_batches(html_chunks, issues, client, model, max_tokens):
    verified = []
    sys_prompt = (
        'You are a code auditor. Given a small set of HTML code snippets and up to 10 audit issues, '
        'for each issue respond with {"issue","confirmed":true/false,"explanation"}. '
        'Return only a JSON array of these objects.'
    )
    for batch in batch_list(issues, 10):
        # Select only relevant HTML chunks based on issue selector or keywords
        relevant = []
        for issue in batch:
            keyword = issue.get('selector') or issue.get('issue','').split()[0]
            for chunk in html_chunks:
                if keyword and keyword in chunk:
                    relevant.append(chunk)
        # Deduplicate and limit to first 3 chunks to reduce token usage
        html_parts = list(dict.fromkeys(relevant))[:3]
        if not html_parts:
            # fallback: use first chunk
            html_parts = [html_chunks[0]]

        payload = json.dumps({
            'html_chunks': html_parts,
            'issues': batch
        })
        # Rate-limit and JSON extract
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{'role':'system','content':sys_prompt}, {'role':'user','content':payload}],
                    temperature=0, max_tokens=max_tokens
                )
                text = resp.choices[0].message.content
                match = re.search(r'(\[.*?\])', text, re.S)
                if match:
                    verified.extend(json.loads(match.group(1)))
                break
            except Exception as e:
                if 'rate limit' in str(e).lower():
                    time.sleep(2 ** attempt)
                    continue
                else:
                    break
        time.sleep(1)
    return verified

# Main entry point
if __name__=='__main__':
    parser = argparse.ArgumentParser(description='Combined image + code audit in multiple steps')
    parser.add_argument('--api-key', help='OpenAI API key or env OPENAI_API_KEY')
    parser.add_argument('--url', required=True, help='Product page URL')
    parser.add_argument('--img', required=True, help='Screenshot image path')
    parser.add_argument('--checklist', required=True, help='Checklist text file path')
    parser.add_argument('--model-image', default='gpt-4o-mini', help='LLM for image analysis')
    parser.add_argument('--model-code', default='gpt-4.1', help='LLM for code verification')
    parser.add_argument('--max-tokens', type=int, default=500, help='Max tokens per call')
    args = parser.parse_args()

    key = args.api_key or os.getenv('OPENAI_API_KEY')
    if not key:
        parser.error('Provide --api-key or set OPENAI_API_KEY env var')
    client = OpenAI(api_key=key)

    # Step 1: image analysis
    thumb = create_thumbnail(args.img)
    checklist = load_checklist(args.checklist)
    issues = analyze_image_batches(thumb, checklist, client, args.model_image, args.max_tokens)
    print('Image Analysis Results:', json.dumps(issues, indent=2))

    # Step 2: code verification
    html_chunks = fetch_and_split_html(args.url)
    verified = analyze_code_batches(html_chunks, issues, client, args.model_code, args.max_tokens)
    print('Code Verification Results:', json.dumps(verified, indent=2))
