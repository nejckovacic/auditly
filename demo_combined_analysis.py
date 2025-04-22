# demo_combined_analysis.py
# To run:
# python demo_combined_analysis.py --api-key YOUR_KEY \
#    --url "https://example.com/product" --img screenshot.png --checklist checklist.txt

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

# Utility: split list into batches of size N
def batch_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i+size]

# Generate Base64 thumbnail from full-page screenshot and save locally at higher quality
# Removes resizing for full-resolution, compressing to JPEG quality=90, then truncates Base64
def create_and_save_thumbnail(path, thumb_path="thumbnail.jpg", quality=90, max_bytes=50*1024):  # reduce Base64 payload to ~50KB
    img = Image.open(path).convert('RGB')
    img.save(thumb_path, format='JPEG', quality=quality)
    with open(thumb_path, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    return b64[:max_bytes], thumb_path
    img = Image.open(path).convert('RGB')
    # Save full-resolution or large thumbnail
    img.save(thumb_path, format='JPEG', quality=quality)
    with open(thumb_path, 'rb') as f:
        data = f.read()
    b64 = base64.b64encode(data).decode()
    return b64[:max_bytes], thumb_path

# Load checklist items (one per line)
def load_checklist(path):
    with open(path, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

# Fetch HTML and split into chunks by line count, preserving start line
def fetch_and_split_html(url, lines_per_chunk=50):
    r = requests.get(url, headers={'User-Agent':'AuditlyBot/1.0'})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script','style','noscript','iframe']): tag.decompose()
    cleaned = soup.prettify()
    lines = cleaned.splitlines()
    chunks = []
    for i in range(0, len(lines), lines_per_chunk):
        chunk_lines = lines[i:i+lines_per_chunk]
        chunks.append({
            'name': f'chunk_{i//lines_per_chunk+1}',
            'start_line': i+1,
            'html': '\n'.join(chunk_lines)
        })
    return chunks

# Step 1: Image-based analysis in 10-item batches
def analyze_image_batches(thumbnail, checklist, client, model, max_tokens):
    issues = []
    prompt = (
        'You are a UX auditor. Given a product page screenshot (Base64) and up to 10 checklist items, '
        'identify missing or suboptimal elements. Return ONLY a JSON array of '
        '{"issue","recommendation","selector","confidence"}.'
    )
    for batch in batch_list(checklist, 10):
        payload = json.dumps({'screenshot': thumbnail, 'checklist': batch})
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role':'system','content':prompt}, {'role':'user','content':payload}],
            temperature=0,
            max_tokens=max_tokens
        )
        text = resp.choices[0].message.content
        m = re.search(r'(\[.*?\])', text, re.S)
        if m:
            try:
                issues.extend(json.loads(m.group(1)))
            except:
                pass
        time.sleep(1)
    return issues

# Step 2: Code verification for flagged issues
def verify_flagged_issues(html_chunks, issues, client, model, max_tokens):
    verified = []
    prompt = (
        'You are a code auditor. Given HTML snippets and up to 10 audit issues, '
        'for each issue return {"issue","confirmed":true/false,"explanation"}. '
        'Return ONLY a JSON array of those objects.'
    )
    for batch in batch_list(issues, 10):
        relevant = []
        for issue in batch:
            key = issue.get('selector') or issue['issue'].split()[0]
            for chunk in html_chunks:
                if key and key in chunk['html']:
                    relevant.append(chunk['html'])
        html_parts = list(dict.fromkeys(relevant))[:3] or [html_chunks[0]['html']]
        payload = json.dumps({'html_chunks': html_parts, 'issues': batch})
        for i in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=[{'role':'system','content':prompt}, {'role':'user','content':payload}],
                    temperature=0,
                    max_tokens=max_tokens
                )
                m = re.search(r'(\[.*?\])', resp.choices[0].message.content, re.S)
                if m:
                    verified.extend(json.loads(m.group(1)))
                break
            except Exception as e:
                if 'rate limit' in str(e).lower(): time.sleep(2**i); continue
                break
        time.sleep(1)
    return verified

# Step 3: Full-code audit with all checklist items to catch missed items
def analyze_code_full(html_chunks, checklist, client, model, max_tokens):
    full_issues = []
    prompt = (
        'You are a code auditor. Given HTML snippets and up to 10 checklist items, '
        'identify missing or suboptimal implementations. Return ONLY a JSON array of '
        '{"issue","recommendation","selector"}.'
    )
    for batch in batch_list(checklist, 10):
        parts = [c['html'] for c in html_chunks[:3]]
        payload = json.dumps({'html_chunks': parts, 'checklist': batch})
        resp = client.chat.completions.create(
            model=model,
            messages=[{'role':'system','content':prompt}, {'role':'user','content':payload}],
            temperature=0,
            max_tokens=max_tokens
        )
        m = re.search(r'(\[.*?\])', resp.choices[0].message.content, re.S)
        if m:
            try:
                full_issues.extend(json.loads(m.group(1)))
            except:
                pass
        time.sleep(1)
    return full_issues

# Main execution
if __name__=='__main__':
    p = argparse.ArgumentParser(description='Combined image + code audit pipeline')
    p.add_argument('--api-key', help='OpenAI API key or set OPENAI_API_KEY')
    p.add_argument('--url', required=True, help='Product page URL')
    p.add_argument('--img', required=True, help='Screenshot image path')
    p.add_argument('--checklist', required=True, help='Checklist text file path')
    p.add_argument('--model-image', default='gpt-4o-mini', help='LLM for image analysis')
    p.add_argument('--model-code', default='gpt-4.1', help='LLM for code verification')
    p.add_argument('--max-tokens', type=int, default=500, help='Max tokens per call')
    args = p.parse_args()

    key = args.api_key or os.getenv('OPENAI_API_KEY')
    if not key:
        p.error('Provide --api-key or set OPENAI_API_KEY')
    client = OpenAI(api_key=key)

    # 1. Image analysis
    thumb, thumb_path = create_and_save_thumbnail(args.img)
    print(f'Thumbnail saved to: {thumb_path}')
    checklist = load_checklist(args.checklist)
    image_issues = analyze_image_batches(thumb, checklist, client, args.model_image, args.max_tokens)
    print('Image Issues:', json.dumps(image_issues, indent=2))

    # 2. Fetch and chunk HTML
    html_chunks = fetch_and_split_html(args.url)

    # 3. Verify flagged issues
    verified = verify_flagged_issues(html_chunks, image_issues, client, args.model_code, args.max_tokens)
    print('Verified Issues:', json.dumps(verified, indent=2))

    # 4. Full code audit
    code_issues = analyze_code_full(html_chunks, checklist, client, args.model_code, args.max_tokens)
    print('Full Code Audit Issues:', json.dumps(code_issues, indent=2))

    # 5. Combine and output final
    final = {"verified": verified, "new_code_issues": code_issues}
    print('Final Results:', json.dumps(final, indent=2))
