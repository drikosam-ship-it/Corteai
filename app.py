import os
import json
import re
import anthropic
import yt_dlp
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

def get_transcript(url):
    ydl_opts = {
        'skip_download': True,
        'writesubtitles': False,
        'writeautomaticsub': False,
        'quiet': True,
        'no_warnings': True,
    }

    # First try to get subtitles/transcript via yt-dlp info
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # Try manual subtitles first, then auto-generated
    subtitles = info.get('subtitles', {})
    auto_subtitles = info.get('automatic_captions', {})

    # Prefer Portuguese, then English
    lang_priority = ['pt', 'pt-BR', 'pt-br', 'en', 'en-US']
    chosen_subs = None
    chosen_lang = None

    for lang in lang_priority:
        if lang in subtitles:
            chosen_subs = subtitles[lang]
            chosen_lang = lang
            break

    if not chosen_subs:
        for lang in lang_priority:
            if lang in auto_subtitles:
                chosen_subs = auto_subtitles[lang]
                chosen_lang = lang
                break

    if not chosen_subs:
        # Try any available language
        if subtitles:
            chosen_lang = list(subtitles.keys())[0]
            chosen_subs = subtitles[chosen_lang]
        elif auto_subtitles:
            chosen_lang = list(auto_subtitles.keys())[0]
            chosen_subs = auto_subtitles[chosen_lang]

    if not chosen_subs:
        raise Exception("Nenhuma transcrição/legenda disponível para este vídeo.")

    # Find json3 format, fallback to vtt
    sub_url = None
    for fmt in chosen_subs:
        if fmt.get('ext') == 'json3':
            sub_url = fmt['url']
            break
    if not sub_url:
        for fmt in chosen_subs:
            if fmt.get('ext') in ('vtt', 'srv1', 'srv2', 'srv3'):
                sub_url = fmt['url']
                break
    if not sub_url and chosen_subs:
        sub_url = chosen_subs[0]['url']

    # Download the subtitle file
    import urllib.request
    with urllib.request.urlopen(sub_url) as response:
        content = response.read().decode('utf-8')

    # Parse based on format
    if 'json3' in sub_url or sub_url.endswith('.json3'):
        return parse_json3(content)
    else:
        return parse_vtt(content)

def parse_json3(content):
    data = json.loads(content)
    texts = []
    for event in data.get('events', []):
        for seg in event.get('segs', []):
            t = seg.get('utf8', '').strip()
            if t and t != '\n':
                texts.append(t)
    return ' '.join(texts)

def parse_vtt(content):
    lines = content.split('\n')
    texts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if '-->' in line:
            continue
        if line.startswith('WEBVTT') or line.startswith('NOTE') or line.isdigit():
            continue
        # Remove HTML tags
        clean = re.sub(r'<[^>]+>', '', line)
        if clean:
            texts.append(clean)
    return ' '.join(texts)

def analyze_with_claude(transcript, content_type, clips_count):
    type_labels = {
        'debate': 'debate/entrevista',
        'podcast': 'podcast',
        'palestra': 'palestra/talk',
        'serie': 'série/documentário'
    }

    label = type_labels.get(content_type, 'vídeo')

    prompt = f"""Você é um especialista em produção de conteúdo viral para redes sociais (Instagram Reels, TikTok, YouTube Shorts). Analise a transcrição abaixo de um {label} e identifique os {clips_count} melhores momentos para cortes virais.

TRANSCRIÇÃO:
{transcript[:14000]}

Para cada corte, retorne um JSON com EXATAMENTE esta estrutura:
{{
  "clips": [
    {{
      "num": 1,
      "titulo": "Título chamativo para o corte (máx 60 chars)",
      "tipo": "Tipo do clipe (ex: Momento de tensão, Resposta icônica, Revelação, Argumento forte, Confronto, Humor, Insight, etc.)",
      "duracao": "estimativa em segundos (ex: 45s)",
      "viral": 5,
      "entrada": "frase exata de entrada — primeiras palavras ditas no início do trecho",
      "saida": "frase exata de saída — últimas palavras ditas no final do trecho",
      "hook": "gancho de abertura para usar na legenda ou narração (1-2 frases impactantes)",
      "legenda": "legenda completa para o post com emojis e call-to-action (3-5 linhas)"
    }}
  ]
}}

Regras:
- viral: nota de 1 a 5 (5 = máximo potencial viral)
- entrada/saida: use frases VERBATIM da transcrição para o editor localizar no vídeo
- Priorize momentos de conflito, revelações, frases de impacto, emoção, humor ou insights únicos
- Ordene do maior para o menor potencial viral
- Retorne APENAS o JSON, sem markdown, sem explicações"""

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    text = message.content[0].text
    clean = re.sub(r'```json|```', '', text).strip()
    return json.loads(clean)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    url = data.get('url', '').strip()
    content_type = data.get('contentType', 'debate')
    clips_count = data.get('clipsCount', 8)

    if not url:
        return jsonify({'error': 'URL não fornecida'}), 400

    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY não configurada no servidor'}), 500

    try:
        transcript = get_transcript(url)
        if len(transcript) < 100:
            return jsonify({'error': 'Transcrição muito curta ou indisponível.'}), 400

        result = analyze_with_claude(transcript, content_type, clips_count)
        return jsonify({'success': True, 'clips': result['clips'], 'transcript_len': len(transcript)})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
