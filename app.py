from flask import (Flask, request, redirect, render_template_string,
                   jsonify, session, send_file, abort)
from functools import wraps
import sqlite3, random, string, os, json, threading, io
import urllib.request, urllib.parse
from datetime import datetime, timezone

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'mude-esta-chave-em-producao')
DB   = os.path.join(os.path.dirname(__file__), 'links.db')
SENHA_ADMIN  = os.environ.get('SENHA_HISTORICO', 'admin123')
API_KEY      = os.environ.get('API_KEY', 'minha-api-key')

# ── Banco de dados ─────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS links (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo     TEXT UNIQUE NOT NULL,
                url        TEXT NOT NULL,
                cliente    TEXT NOT NULL DEFAULT 'Geral',
                alias      TEXT UNIQUE,
                nota       TEXT,
                senha      TEXT,
                expira_em  DATETIME,
                criado     DATETIME DEFAULT CURRENT_TIMESTAMP,
                cliques    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS tags (
                id   INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE NOT NULL
            );
            CREATE TABLE IF NOT EXISTS link_tags (
                link_id INTEGER REFERENCES links(id) ON DELETE CASCADE,
                tag_id  INTEGER REFERENCES tags(id)  ON DELETE CASCADE,
                PRIMARY KEY (link_id, tag_id)
            );
            CREATE TABLE IF NOT EXISTS cliques_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id     INTEGER REFERENCES links(id) ON DELETE CASCADE,
                data_hora   DATETIME DEFAULT CURRENT_TIMESTAMP,
                pais        TEXT DEFAULT '',
                cidade      TEXT DEFAULT '',
                dispositivo TEXT DEFAULT '',
                navegador   TEXT DEFAULT '',
                referencia  TEXT DEFAULT '',
                ip          TEXT DEFAULT ''
            );
        ''')
        for col in ['alias','nota','senha','expira_em']:
            try:
                db.execute(f"ALTER TABLE links ADD COLUMN {col} TEXT")
            except Exception:
                pass
        try:
            db.execute("ALTER TABLE links ADD COLUMN cliente TEXT NOT NULL DEFAULT 'Geral'")
        except Exception:
            pass

def gerar_codigo(n=6):
    chars = string.ascii_letters + string.digits
    with get_db() as db:
        for _ in range(20):
            c = ''.join(random.choices(chars, k=n))
            if not db.execute('SELECT 1 FROM links WHERE codigo=? OR alias=?', (c,c)).fetchone():
                return c
    return ''.join(random.choices(chars, k=8))

def normalizar_cliente(nome):
    return ' '.join((nome or '').split()).title() or 'Geral'

# ── Helpers ────────────────────────────────────────────────────────────
ROTAS_RESERVADAS = {'encurtar','historico','clientes','login','logout',
                    'api','qr','favicon.ico','analytics','tags'}

def parse_ua(ua):
    u = (ua or '').lower()
    if any(x in u for x in ('iphone','android','mobile')):
        dev = 'Mobile'
    elif any(x in u for x in ('ipad','tablet')):
        dev = 'Tablet'
    else:
        dev = 'Desktop'
    if 'edg' in u:          nav = 'Edge'
    elif 'chrome' in u:     nav = 'Chrome'
    elif 'firefox' in u:    nav = 'Firefox'
    elif 'safari' in u:     nav = 'Safari'
    else:                   nav = 'Outro'
    return dev, nav

def get_geo(ip):
    try:
        if not ip or ip.startswith(('127.','10.','192.168.','::1')):
            return '', ''
        url = f'http://ip-api.com/json/{ip}?fields=country,city'
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read())
        return d.get('country',''), d.get('city','')
    except Exception:
        return '', ''

def log_clique(link_id, ip, ua, ref):
    def _run():
        pais, cidade = get_geo(ip)
        dev, nav = parse_ua(ua)
        with get_db() as db:
            db.execute(
                'INSERT INTO cliques_log (link_id,ip,pais,cidade,dispositivo,navegador,referencia)'
                ' VALUES (?,?,?,?,?,?,?)',
                (link_id, ip, pais, cidade, dev, nav, ref or ''))
            db.execute('UPDATE links SET cliques=cliques+1 WHERE id=?', (link_id,))
    threading.Thread(target=_run, daemon=True).start()

def get_tags_link(db, link_id):
    rows = db.execute(
        'SELECT t.nome FROM tags t JOIN link_tags lt ON t.id=lt.tag_id WHERE lt.link_id=?',
        (link_id,)).fetchall()
    return [r['nome'] for r in rows]

def salvar_tags(db, link_id, tags_str):
    db.execute('DELETE FROM link_tags WHERE link_id=?', (link_id,))
    for t in [x.strip() for x in (tags_str or '').split(',') if x.strip()]:
        db.execute('INSERT OR IGNORE INTO tags (nome) VALUES (?)', (t,))
        tag_id = db.execute('SELECT id FROM tags WHERE nome=?', (t,)).fetchone()['id']
        db.execute('INSERT OR IGNORE INTO link_tags (link_id,tag_id) VALUES (?,?)', (link_id, tag_id))

# ── Auth ───────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('admin'):
            return redirect('/login?next=' + request.path)
        return f(*a, **kw)
    return dec

def api_auth(f):
    @wraps(f)
    def dec(*a, **kw):
        key = request.headers.get('X-API-Key') or request.args.get('api_key','')
        if key != API_KEY:
            return jsonify({'erro':'API key inválida'}), 401
        return f(*a, **kw)
    return dec

# ══════════════════════════════════════════════════════════════════════
# CSS BASE
# ══════════════════════════════════════════════════════════════════════
BASE_CSS = '''
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#0f0f1a;min-height:100vh;color:#e0e0ff}
a{color:#a78bfa;text-decoration:none}
a:hover{text-decoration:underline}
label{display:block;color:#9090aa;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:5px}
input,select,textarea{background:#0f0f1a;border:1px solid #2a2a4a;border-radius:10px;padding:11px 14px;color:#fff;font-size:14px;outline:none;transition:border-color .2s;width:100%;font-family:inherit}
input:focus,select:focus,textarea:focus{border-color:#6c63ff}
input::placeholder,textarea::placeholder{color:#3a3a5a}
select option{background:#1a1a2e}
.btn{background:linear-gradient(135deg,#6c63ff,#a855f7);border:none;border-radius:10px;padding:12px 20px;color:#fff;font-size:14px;font-weight:700;cursor:pointer;transition:opacity .2s;white-space:nowrap}
.btn:hover{opacity:.88}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-ghost{background:transparent;border:1px solid #2a2a4a;border-radius:8px;padding:6px 14px;color:#9090aa;font-size:12px;cursor:pointer;transition:all .2s;white-space:nowrap}
.btn-ghost:hover{border-color:#6c63ff;color:#a78bfa}
.btn-ghost.ok{border-color:#4ade80;color:#4ade80}
.badge{display:inline-block;background:#6c63ff22;color:#a78bfa;border-radius:20px;padding:2px 9px;font-size:11px;font-weight:700}
.tag{display:inline-block;background:#2a2a4a;color:#9090aa;border-radius:20px;padding:2px 9px;font-size:11px;margin:1px}
.danger{color:#f87171}
.muted{color:#6b6b8a}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:5px}
@keyframes spin{to{transform:rotate(360deg)}}
'''

# ══════════════════════════════════════════════════════════════════════
# PÁGINA PRINCIPAL
# ══════════════════════════════════════════════════════════════════════
HTML_MAIN = '''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Encurtador de Links — Lobios</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Jost:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --purple:#6C2B87;
  --purple-h:#9040b1;
  --orange:#ff5538;
  --cyan:#11ddf5;
  --blue:#086ad8;
  --text:#4c4d56;
  --dark:#1a1a2a;
}
body{font-family:"Barlow",sans-serif;background:#f5f5f8;color:var(--text);min-height:100vh;display:flex;flex-direction:column}

/* ── NAVBAR ── */
.navbar{background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.08);position:sticky;top:0;z-index:100}
.nav-inner{max-width:1100px;margin:0 auto;padding:0 24px;display:flex;align-items:center;justify-content:space-between;height:70px}
.nav-logo img{height:40px;display:block}
.nav-links{display:flex;gap:8px;align-items:center}
.nav-links a{font-family:"Jost",sans-serif;font-size:14px;font-weight:500;color:var(--text);padding:8px 14px;border-radius:6px;text-decoration:none;transition:color .2s}
.nav-links a:hover{color:var(--purple)}
.nav-links a.active{color:var(--purple);font-weight:700}
.nav-btn{background:var(--purple);color:#fff!important;border-radius:8px;padding:9px 20px!important;font-weight:700!important;transition:background .2s!important}
.nav-btn:hover{background:var(--purple-h)!important}
@media(max-width:640px){.nav-links{display:none}}

/* ── HERO ── */
.hero{background:linear-gradient(135deg,var(--purple) 0%,#3d1058 100%);padding:64px 24px 72px;text-align:center;position:relative;overflow:hidden}
.hero::before{content:'';position:absolute;inset:0;background:url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.03'%3E%3Ccircle cx='30' cy='30' r='20'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");pointer-events:none}
.hero-badge{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:30px;padding:6px 16px;font-family:"Jost",sans-serif;font-size:12px;font-weight:600;color:#fff;letter-spacing:.5px;text-transform:uppercase;margin-bottom:20px}
.hero h1{font-family:"Jost",sans-serif;font-size:clamp(26px,4vw,42px);font-weight:700;color:#fff;line-height:1.2;margin-bottom:12px}
.hero h1 span{color:var(--cyan)}
.hero p{color:rgba(255,255,255,.75);font-size:16px;max-width:520px;margin:0 auto 36px;line-height:1.6}

/* ── CARD DO FORMULÁRIO ── */
.form-card{background:#fff;border-radius:20px;box-shadow:0 20px 60px rgba(108,43,135,.18);padding:36px 32px;max-width:640px;width:100%;margin:0 auto;position:relative}
.form-card label{display:block;font-family:"Jost",sans-serif;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:#888;margin-bottom:6px}
.form-card input,.form-card select,.form-card textarea{width:100%;background:#f8f7fb;border:1.5px solid #e8e0f0;border-radius:10px;padding:11px 14px;color:var(--dark);font-size:14px;font-family:"Barlow",sans-serif;outline:none;transition:border-color .2s}
.form-card input:focus,.form-card select:focus{border-color:var(--purple);background:#fff}
.form-card input::placeholder{color:#bbb}
.fg{margin-bottom:14px}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px}
.input-row{display:flex;gap:10px;margin-bottom:14px}
.input-row input{flex:1}
.btn-main{background:var(--purple);border:none;border-radius:10px;padding:12px 24px;color:#fff;font-size:14px;font-weight:700;font-family:"Jost",sans-serif;cursor:pointer;transition:background .2s;white-space:nowrap;letter-spacing:.3px}
.btn-main:hover{background:var(--purple-h)}
.btn-main:disabled{opacity:.6;cursor:not-allowed}
.btn-ghost{background:#fff;border:1.5px solid #e0d0ec;border-radius:8px;padding:7px 14px;color:var(--purple);font-size:12px;font-weight:700;font-family:"Jost",sans-serif;cursor:pointer;transition:all .2s;white-space:nowrap}
.btn-ghost:hover{border-color:var(--purple);background:#f9f4fc}
.btn-ghost.ok{border-color:#22c55e;color:#22c55e}
.opt-toggle{color:var(--purple);font-size:12px;font-weight:600;cursor:pointer;margin-bottom:12px;display:inline-flex;align-items:center;gap:5px;user-select:none;font-family:"Jost",sans-serif;text-transform:uppercase;letter-spacing:.5px}
.opt-toggle:hover{color:var(--purple-h)}
.opts{display:none}
.opts.open{display:block}
.result-box{display:none;background:linear-gradient(135deg,#f5f0fd,#ede4f8);border:1.5px solid #d4b8ec;border-radius:12px;padding:16px 18px;margin-top:6px}
.result-box.visible{display:block}
.result-row{display:flex;align-items:center;gap:10px;margin-top:8px}
.short-url{flex:1;color:var(--purple);font-size:15px;font-weight:700;text-decoration:none;word-break:break-all;font-family:"Jost",sans-serif}
.short-url:hover{text-decoration:underline}
.error{background:#fff5f5;border:1.5px solid #fcc;border-radius:10px;padding:12px 16px;color:#e53e3e;font-size:13px;margin-top:6px;display:none}
.error.visible{display:block}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:6px}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── SESSÃO ── */
.sess{margin-top:28px;padding-top:20px;border-top:1.5px solid #f0eaf8}
.sess h2{font-family:"Jost",sans-serif;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:#aaa;margin-bottom:12px}
.sess-list{list-style:none;display:flex;flex-direction:column;gap:8px}
.sess-item{background:#f8f7fb;border:1.5px solid #ede5f5;border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px}
.sess-info{flex:1;min-width:0}
.sess-cli{color:var(--purple);font-size:11px;font-weight:700;font-family:"Jost",sans-serif;margin-bottom:2px}
.sess-orig{color:#aaa;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sess-short{color:var(--purple);font-size:13px;font-weight:700;text-decoration:none;font-family:"Jost",sans-serif}
.sess-short:hover{text-decoration:underline}
.empty{color:#ccc;font-size:13px;text-align:center;padding:16px}

/* ── MAIN AREA ── */
.main-area{flex:1;background:#f5f5f8;padding:40px 24px}
.main-area-inner{max-width:1100px;margin:0 auto;display:grid;grid-template-columns:1fr 340px;gap:36px;align-items:start}
@media(max-width:900px){.main-area-inner{grid-template-columns:1fr}}

/* ── INFO CARDS ── */
.info-cards{display:flex;flex-direction:column;gap:16px}
.info-card{background:#fff;border-radius:14px;padding:22px 24px;border-left:4px solid var(--purple);box-shadow:0 2px 12px rgba(0,0,0,.05)}
.info-card-icon{font-size:22px;margin-bottom:8px}
.info-card h3{font-family:"Jost",sans-serif;font-size:14px;font-weight:700;color:var(--dark);margin-bottom:4px}
.info-card p{font-size:13px;color:#888;line-height:1.5}
.info-card:nth-child(2){border-left-color:var(--orange)}
.info-card:nth-child(3){border-left-color:var(--cyan)}

/* ── FOOTER ── */
.footer{background:var(--dark);padding:28px 24px;text-align:center}
.footer-logo img{height:32px;margin:0 auto 12px;display:block}
.footer-text{color:rgba(255,255,255,.4);font-size:12px}
.footer-text a{color:rgba(255,255,255,.6);text-decoration:none}
.footer-text a:hover{color:#fff}
</style>
</head>
<body>

<!-- NAVBAR -->
<nav class="navbar">
  <div class="nav-inner">
    <a class="nav-logo" href="https://lobios.com.br" target="_blank">
      <img src="https://lobios.com.br/assets/images/logo.png" alt="Lobios">
    </a>
    <div class="nav-links">
      <a href="/" class="active">Encurtador</a>
      <a href="/historico" class="nav-btn">Histórico &amp; Analytics</a>
    </div>
  </div>
</nav>

<!-- HERO -->
<section class="hero">
  <div class="hero-badge">🔗 Lobios Link Shortener</div>
  <h1>Encurte seus links.<br><span>Simples e seguro.</span></h1>
  <p>Cole um link longo, defina o cliente, adicione opções avançadas e gere um link curto instantaneamente.</p>

  <!-- FORM CARD dentro do hero -->
  <div class="form-card">
    <div class="fg">
      <label>Cliente</label>
      <input type="text" id="cliente" placeholder="Ex: Empresa ABC" list="cli-list">
      <datalist id="cli-list"></datalist>
    </div>

    <div class="fg">
      <label>Link longo</label>
      <div class="input-row">
        <input type="url" id="inp" placeholder="https://exemplo.com/link/muito/longo...">
        <button class="btn-main" id="btn" onclick="encurtar()">Encurtar</button>
      </div>
    </div>

    <div>
      <span class="opt-toggle" onclick="toggleOpts()">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="3"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/></svg>
        Opções avançadas <span id="opt-arrow">▾</span>
      </span>
    </div>
    <div class="opts" id="opts">
      <div class="row2">
        <div>
          <label>Alias personalizado</label>
          <input type="text" id="alias" placeholder="minha-empresa">
        </div>
        <div>
          <label>Tags (vírgula)</label>
          <input type="text" id="tags" placeholder="marketing, campanha">
        </div>
      </div>
      <div class="row2">
        <div>
          <label>Senha do link</label>
          <input type="password" id="lnk-senha" placeholder="Deixe vazio para público">
        </div>
        <div>
          <label>Expira em</label>
          <input type="date" id="expira">
        </div>
      </div>
      <div class="fg">
        <label>Nota / descrição</label>
        <input type="text" id="nota" placeholder="Ex: campanha de junho">
      </div>
    </div>

    <div class="result-box" id="res">
      <div style="color:#6C2B87;font-size:12px;font-weight:700;font-family:Jost,sans-serif">✅ Link encurtado com sucesso!</div>
      <div class="result-row">
        <a class="short-url" id="short" href="#" target="_blank"></a>
        <button class="btn-ghost" id="btnQr" onclick="abrirQr()">QR Code</button>
        <button class="btn-ghost" id="btnCopy" onclick="copiar()">Copiar</button>
      </div>
    </div>
    <div class="error" id="err"></div>

    <div class="sess">
      <h2>Links desta sessão</h2>
      <ul class="sess-list" id="hist"><li class="empty">Nenhum link encurtado ainda.</li></ul>
    </div>
  </div>
</section>

<!-- INFO CARDS -->
<div class="main-area">
  <div class="main-area-inner">
    <div><!-- espaço para expansão futura --></div>
    <div class="info-cards">
      <div class="info-card">
        <div class="info-card-icon">📊</div>
        <h3>Analytics detalhado</h3>
        <p>Veja país, cidade, dispositivo, navegador e referência por clique.</p>
      </div>
      <div class="info-card">
        <div class="info-card-icon">🔒</div>
        <h3>Links protegidos</h3>
        <p>Adicione senha individual, data de expiração e alias personalizado.</p>
      </div>
      <div class="info-card">
        <div class="info-card-icon">📁</div>
        <h3>Organizado por cliente</h3>
        <p>Separe os links por cliente com histórico e filtros no painel.</p>
      </div>
    </div>
  </div>
</div>

<!-- FOOTER -->
<footer class="footer">
  <div class="footer-logo">
    <img src="https://lobios.com.br/assets/images/logobranca.png" alt="Lobios">
  </div>
  <p class="footer-text">
    © 2024 Lobios — Soluções em Tecnologia da Informação &nbsp;|&nbsp;
    <a href="https://lobios.com.br" target="_blank">lobios.com.br</a>
  </p>
</footer>

<script>
const sessao=[];
fetch('/clientes').then(r=>r.json()).then(l=>{
  const dl=document.getElementById('cli-list');
  l.forEach(c=>{const o=document.createElement('option');o.value=c;dl.appendChild(o);});
});
function toggleOpts(){
  const el=document.getElementById('opts');
  el.classList.toggle('open');
  document.getElementById('opt-arrow').textContent=el.classList.contains('open')?'▴':'▾';
}
async function encurtar(){
  const url=document.getElementById('inp').value.trim();
  const cliente=document.getElementById('cliente').value.trim();
  const alias=document.getElementById('alias').value.trim();
  const tags=document.getElementById('tags').value.trim();
  const senha=document.getElementById('lnk-senha').value;
  const expira=document.getElementById('expira').value;
  const nota=document.getElementById('nota').value.trim();
  const btn=document.getElementById('btn');
  const res=document.getElementById('res');
  const err=document.getElementById('err');
  if(!url){mostrarErro('Cole um link antes de encurtar.');return;}
  if(!/^https?:\\/\\//.test(url)){mostrarErro('O link deve começar com http:// ou https://');return;}
  res.classList.remove('visible');err.classList.remove('visible');
  btn.disabled=true;btn.innerHTML='<span class="spinner"></span>Encurtando...';
  try{
    const r=await fetch('/encurtar',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({url,cliente,alias,tags,senha,expira_em:expira,nota})});
    const d=await r.json();
    if(!r.ok)throw new Error(d.erro||'Erro');
    document.getElementById('short').textContent=d.curto;
    document.getElementById('short').href=d.curto;
    document.getElementById('btnQr').dataset.codigo=d.codigo;
    res.classList.add('visible');
    document.getElementById('inp').value='';
    if(!sessao.find(i=>i.curto===d.curto))
      sessao.unshift({original:url,curto:d.curto,cliente:cliente||'Geral',codigo:d.codigo});
    renderHist();
  }catch(e){mostrarErro(e.message);}
  finally{btn.disabled=false;btn.textContent='Encurtar';}
}
function mostrarErro(msg){
  const el=document.getElementById('err');el.textContent='⚠️ '+msg;el.classList.add('visible');
}
function copiar(url,btn){
  const u=url||document.getElementById('short').textContent;
  const b=btn||document.getElementById('btnCopy');
  navigator.clipboard.writeText(u).then(()=>{
    const o=b.textContent;b.textContent='✓';b.classList.add('ok');
    setTimeout(()=>{b.textContent=o;b.classList.remove('ok');},2000);
  });
}
function abrirQr(codigo){
  const c=codigo||document.getElementById('btnQr').dataset.codigo;
  if(c)window.open('/qr/'+c,'_blank');
}
function renderHist(){
  const ul=document.getElementById('hist');
  if(!sessao.length){ul.innerHTML='<li class="empty">Nenhum link encurtado ainda.</li>';return;}
  ul.innerHTML=sessao.map(it=>`
    <li class="sess-item">
      <div class="sess-info">
        <div class="sess-cli">📁 ${esc(it.cliente)}</div>
        <div class="sess-orig">${esc(it.original)}</div>
        <a class="sess-short" href="${esc(it.curto)}" target="_blank">${esc(it.curto)}</a>
      </div>
      <div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn-ghost" onclick="abrirQr('${esc(it.codigo)}')">QR</button>
        <button class="btn-ghost" onclick="copiar('${esc(it.curto)}',this)">Copiar</button>
      </div>
    </li>`).join('');
}
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
document.getElementById('inp').addEventListener('keydown',e=>{if(e.key==='Enter')encurtar();});
</script>
</body>
</html>'''

# ══════════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════════
HTML_LOGIN = '''<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login</title>
<style>''' + BASE_CSS + '''
body{display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:18px;padding:44px 36px;width:100%;max-width:360px}
h1{color:#fff;font-size:20px;font-weight:700;text-align:center;margin-bottom:6px}
.sub{color:#6b6b8a;font-size:13px;text-align:center;margin-bottom:28px}
.fg{margin-bottom:14px}
.err{color:#f87171;font-size:13px;margin-bottom:12px;{% if not erro %}display:none{% endif %}}
</style></head><body>
<div class="card">
  <h1>🔒 Área restrita</h1>
  <p class="sub">Histórico e analytics</p>
  <p class="err">Senha incorreta. Tente novamente.</p>
  <form method="POST">
    <input type="hidden" name="next" value="{{ next }}">
    <div class="fg"><label>Senha</label><input type="password" name="senha" autofocus></div>
    <button type="submit" class="btn" style="width:100%">Entrar</button>
  </form>
</div></body></html>'''

# ══════════════════════════════════════════════════════════════════════
# HISTÓRICO
# ══════════════════════════════════════════════════════════════════════
HTML_HIST = '''<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Histórico</title>
<style>''' + BASE_CSS + '''
.layout{display:flex;min-height:100vh}
.sidebar{width:210px;min-width:210px;background:#13132a;border-right:1px solid #1e1e35;padding:24px 0;position:sticky;top:0;height:100vh;overflow-y:auto}
.sb-title{color:#6b6b8a;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;padding:0 18px 10px}
.sb-item{display:block;padding:9px 18px;color:#9090aa;font-size:13px;cursor:pointer;transition:all .15s;text-decoration:none;border-left:3px solid transparent}
.sb-item:hover{background:#1a1a2e;color:#fff;text-decoration:none}
.sb-item.active{background:#1a1a2e;color:#a78bfa;border-left-color:#6c63ff;font-weight:600}
.sb-count{float:right;background:#2a2a4a;border-radius:20px;padding:1px 7px;font-size:10px;color:#6b6b8a}
.sb-sep{border:none;border-top:1px solid #1e1e35;margin:10px 0}
.main{flex:1;padding:32px 36px;overflow-x:auto}
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.top h1{color:#fff;font-size:18px;font-weight:700}
.top-actions{display:flex;gap:12px;align-items:center}
.filters{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.filters input,.filters select{width:auto;min-width:140px;padding:8px 12px;font-size:13px}
.stats-row{display:flex;gap:16px;margin-bottom:24px;flex-wrap:wrap}
.stat-card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:16px 20px;min-width:130px}
.stat-val{color:#fff;font-size:22px;font-weight:700}
.stat-lbl{color:#6b6b8a;font-size:11px;margin-top:2px}
table{width:100%;border-collapse:collapse;min-width:700px}
thead th{text-align:left;color:#6b6b8a;font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:0 12px 8px;font-weight:700}
tbody tr{background:#1a1a2e;border-bottom:1px solid #0f0f1a;transition:background .15s}
tbody tr:hover{background:#1e1e38}
td{padding:10px 12px;vertical-align:middle}
.td-orig{color:#4a4a6a;font-size:11px;max-width:200px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.td-short a{color:#a78bfa;font-size:12px;font-weight:600;text-decoration:none}
.td-short a:hover{text-decoration:underline}
.td-meta{color:#6b6b8a;font-size:11px}
.td-actions{display:flex;gap:5px}
.expired{opacity:.5}
.pill-lock{background:#f8717122;color:#f87171;border-radius:20px;padding:1px 7px;font-size:10px}
.pill-exp{background:#fbbf2422;color:#fbbf24;border-radius:20px;padding:1px 7px;font-size:10px}
.empty-state{color:#4a4a6a;text-align:center;padding:60px;font-size:14px}
</style></head><body>
<div class="layout">
<nav class="sidebar">
  <div class="sb-title">📁 Clientes</div>
  <a class="sb-item {{ 'active' if not filtro_cliente else '' }}" href="/historico">Todos <span class="sb-count">{{ total_geral }}</span></a>
  {% for c in clientes %}
  <a class="sb-item {{ 'active' if filtro_cliente==c.nome else '' }}"
     href="/historico?cliente={{ c.nome|urlencode }}">{{ c.nome }} <span class="sb-count">{{ c.qtd }}</span></a>
  {% endfor %}
  <hr class="sb-sep">
  <div class="sb-title">🏷 Tags</div>
  {% for t in todas_tags %}
  <a class="sb-item {{ 'active' if filtro_tag==t else '' }}"
     href="/historico?tag={{ t|urlencode }}">{{ t }}</a>
  {% endfor %}
</nav>
<div class="main">
  <div class="top">
    <h1>{{ filtro_cliente or filtro_tag or 'Todos os links' }}</h1>
    <div class="top-actions">
      <a href="/" style="font-size:13px">← Encurtar novo</a>
      <a href="/logout" style="font-size:13px;color:#f87171">Sair</a>
    </div>
  </div>
  <div class="filters">
    <form method="GET" style="display:flex;gap:8px;flex-wrap:wrap">
      {% if filtro_cliente %}<input type="hidden" name="cliente" value="{{ filtro_cliente }}">{% endif %}
      {% if filtro_tag %}<input type="hidden" name="tag" value="{{ filtro_tag }}">{% endif %}
      <input name="q" value="{{ q }}" placeholder="Buscar link ou nota...">
      <button type="submit" class="btn-ghost">Filtrar</button>
      {% if q %}<a href="/historico{% if filtro_cliente %}?cliente={{ filtro_cliente|urlencode }}{% elif filtro_tag %}?tag={{ filtro_tag|urlencode }}{% endif %}" class="btn-ghost">Limpar</a>{% endif %}
    </form>
  </div>
  <div class="stats-row">
    <div class="stat-card"><div class="stat-val">{{ links|length }}</div><div class="stat-lbl">Links</div></div>
    <div class="stat-card"><div class="stat-val">{{ cliques_total }}</div><div class="stat-lbl">Cliques totais</div></div>
  </div>
  {% if links %}
  <table>
    <thead><tr>
      <th>Link original</th><th>Link curto</th><th>Tags / Nota</th>
      <th style="text-align:center">Cliques</th><th>Criado</th><th></th>
    </tr></thead>
    <tbody>
    {% for l in links %}
    {% set expirado = l.expira_em and l.expira_em < now %}
    <tr class="{{ 'expired' if expirado else '' }}">
      <td><div class="td-orig" title="{{ l.url }}">{{ l.url }}</div>
        {% if l.nota %}<div style="color:#6b6b8a;font-size:11px;margin-top:2px">💬 {{ l.nota }}</div>{% endif %}
      </td>
      <td class="td-short">
        <a href="/{{ l.alias or l.codigo }}" target="_blank">{{ base }}/{{ l.alias or l.codigo }}</a>
        {% if l.senha %}<span class="pill-lock">🔒 senha</span>{% endif %}
        {% if expirado %}<span class="pill-exp">expirado</span>{% elif l.expira_em %}<span class="pill-exp">exp: {{ l.expira_em[:10] }}</span>{% endif %}
      </td>
      <td class="td-meta">
        {% for t in l._tags %}<span class="tag">{{ t }}</span>{% endfor %}
      </td>
      <td style="text-align:center"><span class="badge">{{ l.cliques }}</span></td>
      <td class="td-meta">{{ l.criado[:10] }}</td>
      <td>
        <div class="td-actions">
          <a href="/analytics/{{ l.codigo }}" class="btn-ghost">📊</a>
          <a href="/qr/{{ l.codigo }}" target="_blank" class="btn-ghost">QR</a>
          <button class="btn-ghost" onclick="copiar('{{ base }}/{{ l.alias or l.codigo }}',this)">Copiar</button>
        </div>
      </td>
    </tr>
    {% endfor %}
    </tbody>
  </table>
  {% else %}
  <p class="empty-state">Nenhum link encontrado.</p>
  {% endif %}
</div></div>
<script>
function copiar(url,btn){
  navigator.clipboard.writeText(url).then(()=>{
    const o=btn.textContent;btn.textContent='✓';btn.classList.add('ok');
    setTimeout(()=>{btn.textContent=o;btn.classList.remove('ok');},2000);
  });
}
</script></body></html>'''

# ══════════════════════════════════════════════════════════════════════
# ANALYTICS POR LINK
# ══════════════════════════════════════════════════════════════════════
HTML_ANALYTICS = '''<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Analytics</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>''' + BASE_CSS + '''
body{padding:32px 20px}
.wrap{max-width:900px;margin:0 auto}
.back{font-size:13px;display:block;margin-bottom:20px}
.header{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:14px;padding:20px 24px;margin-bottom:24px}
.link-url{color:#a78bfa;font-size:15px;font-weight:700;word-break:break-all}
.link-orig{color:#6b6b8a;font-size:12px;margin-top:4px;word-break:break-all}
.meta{color:#6b6b8a;font-size:12px;margin-top:8px;display:flex;gap:16px;flex-wrap:wrap}
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:14px;margin-bottom:24px}
.stat-card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:16px 18px}
.stat-val{color:#fff;font-size:22px;font-weight:700}
.stat-lbl{color:#6b6b8a;font-size:11px;margin-top:2px}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.chart-card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:18px}
.chart-title{color:#9090aa;font-size:11px;text-transform:uppercase;letter-spacing:.5px;font-weight:700;margin-bottom:12px}
canvas{max-height:200px}
.log-table{width:100%;border-collapse:collapse}
.log-table th{text-align:left;color:#6b6b8a;font-size:10px;text-transform:uppercase;padding:0 10px 8px;letter-spacing:.5px}
.log-table td{padding:8px 10px;color:#9090aa;font-size:12px;border-bottom:1px solid #1e1e35}
.log-table tr:hover td{background:#1a1a2e}
@media(max-width:600px){.charts{grid-template-columns:1fr}}
</style></head><body>
<div class="wrap">
  <a class="back" href="/historico">← Voltar ao histórico</a>
  <div class="header">
    <div class="link-url">{{ base }}/{{ link.alias or link.codigo }}</div>
    <div class="link-orig">→ {{ link.url }}</div>
    <div class="meta">
      <span>📁 {{ link.cliente }}</span>
      <span>📅 criado {{ link.criado[:10] }}</span>
      {% if link.expira_em %}<span class="pill-exp">⏳ expira {{ link.expira_em[:10] }}</span>{% endif %}
      {% if link.senha %}<span class="pill-lock">🔒 protegido por senha</span>{% endif %}
      {% if link.nota %}<span>💬 {{ link.nota }}</span>{% endif %}
    </div>
  </div>
  <div class="stats-row">
    <div class="stat-card"><div class="stat-val">{{ link.cliques }}</div><div class="stat-lbl">Total de cliques</div></div>
    <div class="stat-card"><div class="stat-val">{{ paises|length }}</div><div class="stat-lbl">Países</div></div>
    <div class="stat-card"><div class="stat-val">{{ dispositivos|length }}</div><div class="stat-lbl">Tipos de dispositivo</div></div>
  </div>
  <div class="charts">
    <div class="chart-card"><div class="chart-title">Países</div><canvas id="cPais"></canvas></div>
    <div class="chart-card"><div class="chart-title">Dispositivos</div><canvas id="cDev"></canvas></div>
    <div class="chart-card"><div class="chart-title">Navegadores</div><canvas id="cNav"></canvas></div>
    <div class="chart-card"><div class="chart-title">Referências</div><canvas id="cRef"></canvas></div>
  </div>
  {% if logs %}
  <div class="chart-card" style="margin-bottom:16px">
    <div class="chart-title">Últimos 50 cliques</div>
    <table class="log-table">
      <thead><tr><th>Data/Hora</th><th>País</th><th>Cidade</th><th>Dispositivo</th><th>Navegador</th><th>Referência</th></tr></thead>
      <tbody>
      {% for l in logs %}
      <tr>
        <td>{{ l.data_hora[:16].replace('T',' ') }}</td>
        <td>{{ l.pais or '—' }}</td><td>{{ l.cidade or '—' }}</td>
        <td>{{ l.dispositivo or '—' }}</td><td>{{ l.navegador or '—' }}</td>
        <td>{{ l.referencia[:40] if l.referencia else '—' }}</td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
<script>
const COLORS=['#6c63ff','#a855f7','#4ade80','#fbbf24','#f87171','#38bdf8','#fb923c','#a3e635'];
function pie(id,labels,data){
  if(!data.length)return;
  new Chart(document.getElementById(id),{type:'doughnut',
    data:{labels,datasets:[{data,backgroundColor:COLORS,borderWidth:0}]},
    options:{plugins:{legend:{position:'right',labels:{color:'#9090aa',font:{size:11},boxWidth:12}}},
             responsive:true,maintainAspectRatio:true}});
}
pie('cPais',{{ paises_labels|tojson }},{{ paises_data|tojson }});
pie('cDev',{{ dev_labels|tojson }},{{ dev_data|tojson }});
pie('cNav',{{ nav_labels|tojson }},{{ nav_data|tojson }});
pie('cRef',{{ ref_labels|tojson }},{{ ref_data|tojson }});
</script></body></html>'''

# ══════════════════════════════════════════════════════════════════════
# GATE DE SENHA DO LINK
# ══════════════════════════════════════════════════════════════════════
HTML_GATE = '''<!DOCTYPE html><html lang="pt-BR"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Link protegido</title>
<style>''' + BASE_CSS + '''
body{display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:18px;padding:40px 32px;width:100%;max-width:360px;text-align:center}
h1{color:#fff;font-size:20px;margin-bottom:6px}
.sub{color:#6b6b8a;font-size:13px;margin-bottom:24px}
.fg{margin-bottom:14px;text-align:left}
.err{color:#f87171;font-size:13px;margin-bottom:10px}
</style></head><body>
<div class="card">
  <h1>🔒 Link protegido</h1>
  <p class="sub">Digite a senha para acessar</p>
  {% if erro %}<p class="err">Senha incorreta.</p>{% endif %}
  <form method="POST">
    <div class="fg"><label>Senha</label><input type="password" name="senha" autofocus></div>
    <button type="submit" class="btn" style="width:100%">Acessar</button>
  </form>
</div></body></html>'''

# ══════════════════════════════════════════════════════════════════════
# ROTAS
# ══════════════════════════════════════════════════════════════════════
@app.route('/')
def index():
    return render_template_string(HTML_MAIN)

@app.route('/clientes')
def listar_clientes():
    with get_db() as db:
        rows = db.execute("SELECT DISTINCT cliente FROM links ORDER BY cliente").fetchall()
    return jsonify([r['cliente'] for r in rows])

@app.route('/encurtar', methods=['POST'])
def encurtar():
    d       = request.get_json(silent=True) or {}
    url     = (d.get('url') or '').strip()
    cliente = normalizar_cliente(d.get('cliente',''))
    alias   = (d.get('alias') or '').strip().lower() or None
    nota    = (d.get('nota') or '').strip() or None
    senha   = (d.get('senha') or '').strip() or None
    expira  = (d.get('expira_em') or '').strip() or None
    tags_s  = d.get('tags','')

    if not url:
        return jsonify({'erro':'URL não informada'}), 400
    if not url.startswith(('http://','https://')):
        return jsonify({'erro':'URL deve começar com http:// ou https://'}), 400

    if alias and alias in ROTAS_RESERVADAS:
        return jsonify({'erro':'Alias reservado, escolha outro.'}), 400

    with get_db() as db:
        if alias:
            dup = db.execute('SELECT id FROM links WHERE alias=? OR codigo=?', (alias,alias)).fetchone()
            if dup:
                return jsonify({'erro':'Alias já em uso, escolha outro.'}), 400

        row = db.execute(
            'SELECT id,codigo,alias FROM links WHERE url=? AND LOWER(cliente)=LOWER(?)',
            (url, cliente)).fetchone()

        if row:
            codigo = row['codigo']
            link_id = row['id']
            # Atualiza campos se fornecidos
            if alias or nota or senha or expira or tags_s:
                db.execute('UPDATE links SET alias=COALESCE(?,alias), nota=COALESCE(?,nota),'
                           ' senha=COALESCE(?,senha), expira_em=COALESCE(?,expira_em) WHERE id=?',
                           (alias, nota, senha, expira, link_id))
                salvar_tags(db, link_id, tags_s)
        else:
            codigo  = alias or gerar_codigo()
            cur = db.execute(
                'INSERT INTO links (codigo,url,cliente,alias,nota,senha,expira_em) VALUES (?,?,?,?,?,?,?)',
                (codigo, url, cliente, alias, nota, senha, expira))
            link_id = cur.lastrowid
            if not alias:
                db.execute('UPDATE links SET alias=NULL WHERE id=?', (link_id,))
            salvar_tags(db, link_id, tags_s)

    base = request.host_url.rstrip('/')
    short_code = alias or codigo
    return jsonify({'curto': f'{base}/{short_code}', 'codigo': codigo})

@app.route('/<codigo>', methods=['GET','POST'])
def redirecionar(codigo):
    if codigo in ROTAS_RESERVADAS:
        abort(404)
    with get_db() as db:
        row = db.execute(
            'SELECT * FROM links WHERE codigo=? OR alias=?', (codigo, codigo)).fetchone()
    if not row:
        return 'Link não encontrado.', 404

    # Verificar expiração
    if row['expira_em']:
        try:
            exp = datetime.fromisoformat(row['expira_em'])
            if datetime.now() > exp:
                return 'Este link expirou.', 410
        except Exception:
            pass

    # Verificar senha
    if row['senha']:
        if request.method == 'POST':
            if request.form.get('senha') == row['senha']:
                session[f'gate_{row["id"]}'] = True
            else:
                return render_template_string(HTML_GATE, erro=True)
        if not session.get(f'gate_{row["id"]}'):
            return render_template_string(HTML_GATE, erro=False)

    ip  = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
    ua  = request.headers.get('User-Agent', '')
    ref = request.referrer or ''
    log_clique(row['id'], ip, ua, ref)

    return redirect(row['url'], code=302)

@app.route('/qr/<codigo>')
def qr_code(codigo):
    try:
        import qrcode, io as _io
        with get_db() as db:
            row = db.execute('SELECT alias,codigo FROM links WHERE codigo=? OR alias=?',
                             (codigo,codigo)).fetchone()
        if not row:
            return 'Not found', 404
        short_code = row['alias'] or row['codigo']
        url = request.host_url.rstrip('/') + '/' + short_code
        img = qrcode.make(url)
        buf = _io.BytesIO()
        img.save(buf, 'PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png',
                         download_name=f'qr-{short_code}.png')
    except ImportError:
        return 'Instale qrcode[pil] no requirements.txt', 500

@app.route('/login', methods=['GET','POST'])
def login():
    nxt = request.args.get('next', '/historico')
    if request.method == 'POST':
        nxt = request.form.get('next', '/historico')
        if request.form.get('senha') == SENHA_ADMIN:
            session['admin'] = True
            return redirect(nxt)
        return render_template_string(HTML_LOGIN, erro=True, next=nxt)
    return render_template_string(HTML_LOGIN, erro=False, next=nxt)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/historico')
@login_required
def historico():
    filtro_cliente = request.args.get('cliente','').strip()
    filtro_tag     = request.args.get('tag','').strip()
    q              = request.args.get('q','').strip()
    base           = request.host_url.rstrip('/')
    now            = datetime.now().isoformat()

    with get_db() as db:
        clientes = db.execute(
            "SELECT cliente as nome, COUNT(*) as qtd FROM links GROUP BY cliente ORDER BY cliente"
        ).fetchall()
        total_geral = db.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        todas_tags  = [r['nome'] for r in db.execute("SELECT nome FROM tags ORDER BY nome").fetchall()]

        query = "SELECT l.* FROM links l"
        params = []
        where = []
        if filtro_tag:
            query += " JOIN link_tags lt ON l.id=lt.link_id JOIN tags t ON t.id=lt.tag_id"
            where.append("t.nome=?"); params.append(filtro_tag)
        if filtro_cliente:
            where.append("LOWER(l.cliente)=LOWER(?)"); params.append(filtro_cliente)
        if q:
            where.append("(l.url LIKE ? OR l.nota LIKE ? OR l.alias LIKE ?)")
            params += [f'%{q}%', f'%{q}%', f'%{q}%']
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY l.id DESC"

        links = db.execute(query, params).fetchall()
        links = [dict(l) for l in links]
        for l in links:
            l['_tags'] = get_tags_link(db, l['id'])

    cliques_total = sum(l['cliques'] for l in links)
    return render_template_string(HTML_HIST,
        links=links, clientes=clientes, filtro_cliente=filtro_cliente,
        filtro_tag=filtro_tag, todas_tags=todas_tags, q=q,
        cliques_total=cliques_total, total_geral=total_geral, base=base, now=now)

@app.route('/analytics/<codigo>')
@login_required
def analytics(codigo):
    base = request.host_url.rstrip('/')
    with get_db() as db:
        link = db.execute('SELECT * FROM links WHERE codigo=?', (codigo,)).fetchone()
        if not link:
            return 'Link não encontrado', 404
        logs = db.execute(
            'SELECT * FROM cliques_log WHERE link_id=? ORDER BY id DESC LIMIT 50',
            (link['id'],)).fetchall()

        def agg(field):
            rows = db.execute(
                f'SELECT {field}, COUNT(*) as n FROM cliques_log WHERE link_id=? AND {field}!="" GROUP BY {field} ORDER BY n DESC LIMIT 8',
                (link['id'],)).fetchall()
            return [r[field] for r in rows], [r['n'] for r in rows]

        paises_labels, paises_data   = agg('pais')
        dev_labels,    dev_data      = agg('dispositivo')
        nav_labels,    nav_data      = agg('navegador')
        ref_labels,    ref_data      = agg('referencia')

    return render_template_string(HTML_ANALYTICS,
        link=dict(link), logs=logs, base=base,
        paises_labels=paises_labels, paises_data=paises_data,
        dev_labels=dev_labels, dev_data=dev_data,
        nav_labels=nav_labels, nav_data=nav_data,
        ref_labels=ref_labels, ref_data=ref_data,
        paises=paises_labels, dispositivos=dev_labels)

# ── API REST ───────────────────────────────────────────────────────────
@app.route('/api/v1/links', methods=['POST'])
@api_auth
def api_criar():
    d       = request.get_json(silent=True) or {}
    url     = (d.get('url') or '').strip()
    cliente = normalizar_cliente(d.get('cliente',''))
    alias   = (d.get('alias') or '').strip() or None
    nota    = (d.get('nota') or '').strip() or None
    senha   = (d.get('senha') or '').strip() or None
    expira  = (d.get('expira_em') or '').strip() or None
    tags_s  = d.get('tags','')

    if not url:
        return jsonify({'erro':'URL não informada'}), 400
    if not url.startswith(('http://','https://')):
        return jsonify({'erro':'URL inválida'}), 400

    with get_db() as db:
        codigo = alias or gerar_codigo()
        if alias:
            if db.execute('SELECT 1 FROM links WHERE alias=? OR codigo=?',(alias,alias)).fetchone():
                return jsonify({'erro':'Alias já em uso'}), 409
        cur = db.execute(
            'INSERT OR IGNORE INTO links (codigo,url,cliente,alias,nota,senha,expira_em) VALUES (?,?,?,?,?,?,?)',
            (codigo, url, cliente, alias, nota, senha, expira))
        if cur.rowcount:
            salvar_tags(db, cur.lastrowid, tags_s)

    base = request.host_url.rstrip('/')
    return jsonify({'curto': f'{base}/{alias or codigo}', 'codigo': codigo}), 201

@app.route('/api/v1/links', methods=['GET'])
@api_auth
def api_listar():
    with get_db() as db:
        links = db.execute('SELECT codigo,alias,url,cliente,cliques,criado FROM links ORDER BY id DESC LIMIT 100').fetchall()
    base = request.host_url.rstrip('/')
    return jsonify([{**dict(l), 'curto': f'{base}/{l["alias"] or l["codigo"]}'} for l in links])

# ── Inicialização ──────────────────────────────────────────────────────
init_db()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
