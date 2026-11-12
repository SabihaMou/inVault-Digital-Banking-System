"""FinVault: local educational banking simulator, Python 3.10+."""
import base64, hashlib, hmac, html, json, os, secrets, sqlite3, threading, time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT=Path(__file__).resolve().parent
DB=Path(os.environ.get('FINVAULT_DB',str(ROOT/'finvault.sqlite3')))
SESSIONS={}; LOCK=threading.RLock()

def conn():
    c=sqlite3.connect(str(DB),timeout=15,isolation_level=None)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=15000')
    return c

def init_db():
    with conn() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL DEFAULT 'customer',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS accounts(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),number TEXT UNIQUE NOT NULL,status TEXT NOT NULL DEFAULT 'active',currency TEXT NOT NULL DEFAULT 'BDT');
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,reference TEXT UNIQUE NOT NULL,description TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS entries(id INTEGER PRIMARY KEY,transaction_id INTEGER NOT NULL REFERENCES transactions(id),account_id INTEGER NOT NULL REFERENCES accounts(id),amount INTEGER NOT NULL CHECK(amount != 0));
        CREATE TABLE IF NOT EXISTS transfer_keys(user_id INTEGER NOT NULL,key TEXT NOT NULL,transaction_id INTEGER NOT NULL REFERENCES transactions(id),PRIMARY KEY(user_id,key));
        CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),name TEXT NOT NULL,target INTEGER NOT NULL CHECK(target>0),saved INTEGER NOT NULL DEFAULT 0 CHECK(saved>=0));
        CREATE TABLE IF NOT EXISTS budgets(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),category TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0));
        CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL REFERENCES users(id),last4 TEXT NOT NULL,frozen INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,user_id INTEGER,action TEXT NOT NULL,detail TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_entries_account ON entries(account_id);
        CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);''')

def password_hash(p):
    salt=secrets.token_bytes(16);v=hashlib.scrypt(p.encode(),salt=salt,n=2**14,r=8,p=1)
    return base64.b64encode(salt).decode()+':'+base64.b64encode(v).decode()
def password_ok(p,stored):
    try:
        a,b=stored.split(':');return hmac.compare_digest(hashlib.scrypt(p.encode(),salt=base64.b64decode(a),n=2**14,r=8,p=1),base64.b64decode(b))
    except (ValueError,TypeError):return False

def cents(v):
    try:
        d=Decimal(str(v));n=d*100
        if not d.is_finite() or n!=n.to_integral_value() or n<=0 or n>100000000000:return ValueError('Enter a valid positive amount (maximum 1 billion).')
        return int(n)
    except (InvalidOperation,ValueError):return ValueError('Enter a valid monetary amount.')
def money(n):return f'৳ {n/100:,.2f}'
def audit(c,uid,action,detail=''):c.execute('INSERT INTO audit(user_id,action,detail) VALUES(?,?,?)',(uid,action,detail))
def account(c,uid):return c.execute('SELECT * FROM accounts WHERE user_id=?',(uid,)).fetchone()
def balance(c,aid):return c.execute('SELECT COALESCE(SUM(amount),0) FROM entries WHERE account_id=?',(aid,)).fetchone()[0]

def register(username,password,role='customer'):
    username=username.strip().lower()
    if not 3<=len(username)<=32 or not username.replace('_','').isalnum():raise ValueError('Username must be 3–32 letters, digits or underscores.')
    if len(password)<10:raise ValueError('Password must be at least 10 characters.')
    if role not in ('customer','admin','support'):raise ValueError('Invalid role')
    with LOCK,conn() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            cur=c.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',(username,password_hash(password),role));uid=cur.lastrowid
            c.execute('INSERT INTO accounts(user_id,number) VALUES(?,?)',(uid,'FV'+str(uid).zfill(8)))
            c.execute('INSERT INTO cards(user_id,last4) VALUES(?,?)',(uid,str(4000+uid%6000)))
            audit(c,uid,'register');c.execute('COMMIT');return uid
        except Exception:c.execute('ROLLBACK');raise

def post(c,kind,description,lines):
    if sum(amount for _,amount in lines)!=0 or any(amount==0 for _,amount in lines):raise ValueError('Ledger must balance')
    ref='FV-'+secrets.token_hex(6).upper()
    tx=c.execute('INSERT INTO transactions(kind,reference,description) VALUES(?,?,?)',(kind,ref,description)).lastrowid
    c.executemany('INSERT INTO entries(transaction_id,account_id,amount) VALUES(?,?,?)',[(tx,a,v) for a,v in lines]);return tx,ref

def transfer(uid,to_number,amount,key):
    n=cents(amount)
    if isinstance(n,ValueError):raise n
    if not key or len(key)>128:raise ValueError('Invalid transfer request identifier.')
    with LOCK,conn() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            existing=c.execute('SELECT transaction_id FROM transfer_keys WHERE user_id=? AND key=?',(uid,key)).fetchone()
            if existing:
                tx=c.execute('SELECT reference FROM transactions WHERE id=?',(existing[0],)).fetchone();c.execute('COMMIT');return tx[0],True
            src=account(c,uid);dst=c.execute('SELECT * FROM accounts WHERE number=?',(to_number.strip().upper(),)).fetchone()
            if not src or not dst:raise ValueError('Destination account not found.')
            if src['id']==dst['id']:raise ValueError('Cannot transfer to your own account.')
            if src['status']!='active' or dst['status']!='active':raise ValueError('One of the accounts is frozen.')
            if balance(c,src['id'])<n:raise ValueError('Insufficient funds.')
            tx,ref=post(c,'transfer',f'Transfer to {dst["number"]}',[(src['id'],-n),(dst['id'],n)])
            c.execute('INSERT INTO transfer_keys VALUES(?,?,?)',(uid,key,tx));audit(c,uid,'transfer',ref);c.execute('COMMIT');return ref,False
        except Exception:c.execute('ROLLBACK');raise

def seed():
    init_db()
    with conn() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]:return
    for name,role in [('admin','admin'),('support','support'),('alex','customer'),('maya','customer')]:register(name,'DemoPass123!',role)
    with LOCK,conn() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            ids={r['username']:r['id'] for r in c.execute('SELECT id,username FROM users')}
            # Demo treasury account: negative balance is permitted only for this seed-only system account.
            treasury=c.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',('demo_treasury',password_hash(secrets.token_urlsafe(32)),'admin')).lastrowid
            tid=c.execute('INSERT INTO accounts(user_id,number,status) VALUES(?,?,?)',(treasury,'FV00000000','frozen')).lastrowid
            for name,n in [('alex',24875000),('maya',13250000),('admin',5000000),('support',1500000)]:
                a=account(c,ids[name]);post(c,'seed',f'Fictional opening balance for {name}',[(tid,-n),(a['id'],n)])
            c.execute('INSERT INTO goals(user_id,name,target,saved) VALUES(?,?,?,?)',(ids['alex'],'New laptop',12000000,4500000))
            c.execute('INSERT INTO goals(user_id,name,target,saved) VALUES(?,?,?,?)',(ids['alex'],'Travel fund',8000000,2200000))
            c.execute('INSERT INTO budgets(user_id,category,amount) VALUES(?,?,?)',(ids['alex'],'Shopping',2500000))
            audit(c,None,'demo_seed','Fictional data only');c.execute('COMMIT')
        except Exception:c.execute('ROLLBACK');raise

CSS=(ROOT/'static'/'style.css').read_text(encoding='utf8')
def esc(x):return html.escape(str(x),quote=True)
def layout(title,body,user=None,csrf='',notice=''):
    nav=''
    if user:
        links=[('Dashboard','/'),('Transfers','/transfer'),('Transactions','/transactions'),('Savings','/savings'),('Budgets','/budgets'),('Cards','/cards')]
        if user['role']=='admin':links.append(('Administration','/admin'))
        if user['role']=='support':links.append(('Support','/support'))
        nav='<aside class="sidebar"><div class="brand"><span class="brand-icon">◆</span> FinVault <small>PRIVATE BANKING · DEMO</small></div><nav>'+''.join(f'<a href="{p}">{esc(n)}</a>' for n,p in links)+'</nav><div class="side-bottom"><span class="avatar">'+esc(user['username'][0].upper())+'</span><div>'+esc(user['username'])+'<small>'+esc(user['role'].title())+'</small></div><form action="/logout" method="post"><input type="hidden" name="csrf" value="'+esc(csrf)+'"><button class="logout">↗</button></form></div></aside>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+' · FinVault</title><link rel="stylesheet" href="/static/style.css"></head><body><div class="app">'+nav+'<main class="main">'+('<header class="top"><span>FINANCIAL OVERVIEW / '+esc(title.upper())+'</span><span class="pill">● SIMULATION MODE</span></header>' if user else '')+('<div class="notice">'+esc(notice)+'</div>' if notice else '')+body+'<footer>FinVault · Educational simulation · No real funds or banking services</footer></main></div></body></html>'
def field(name,label,type='text',value='',extra=''):return f'<label>{esc(label)}<input name="{esc(name)}" type="{type}" value="{esc(value)}" {extra} required></label>'
def button(label):return '<button class="btn" type="submit">'+esc(label)+' →</button>'
def token(csrf):return '<input type="hidden" name="csrf" value="'+esc(csrf)+'">'
def panel(title,content):return '<section class="panel"><h2>'+esc(title)+'</h2>'+content+'</section>'
def transactions(c,uid,limit=100):
    a=account(c,uid)
    return c.execute('''SELECT t.*,e.amount FROM entries e JOIN transactions t ON t.id=e.transaction_id WHERE e.account_id=? ORDER BY t.id DESC LIMIT ?''',(a['id'],limit)).fetchall()
def table(rows,headers):return '<div class="tablewrap"><table><thead><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr></thead><tbody>'+(''.join('<tr>'+''.join('<td>'+str(v)+'</td>' for v in row)+'</tr>' for row in rows) if rows else '<tr><td colspan="'+str(len(headers))+'">No records yet</td></tr>')+'</tbody></table></div>'

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def respond(self,body,status=200,ctype='text/html; charset=utf-8',cookie=None):
        data=body.encode('utf8') if isinstance(body,str) else body;self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','same-origin');self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'");self.send_header('Cache-Control','no-store')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(data)
    def redirect(self,path,cookie=None):self.send_response(303);self.send_header('Location',path);self.send_header('Cache-Control','no-store');self.send_header('Content-Length','0');self.send_header('Set-Cookie',cookie) if cookie else None;self.end_headers()
    def session(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return None,None
        sid=cookie['sid'].value if 'sid' in cookie else ''
        s=SESSIONS.get(sid)
        if not s or s['expires']<time.time():SESSIONS.pop(sid,None);return None,None
        with conn() as c:u=c.execute('SELECT id,username,role FROM users WHERE id=?',(s['uid'],)).fetchone()
        return u,s
    def do_GET(self):
        p=urlparse(self.path).path
        if p=='/static/style.css':return self.respond(CSS,ctype='text/css; charset=utf-8')
        u,s=self.session()
        if not u:
            if p=='/register':return self.respond(layout('Create account','<div class="auth"><div class="auth-brand">◆ FinVault</div><h1>Create your account</h1><p>Join the fictional FinVault banking environment.</p><form method="post" action="/register">'+field('username','Username')+field('password','Password (10+ characters)','password')+button('Create account')+'</form><p><a href="/login">Already registered? Sign in</a></p></div>'))
            return self.respond(layout('Sign in','<div class="auth"><div class="auth-brand">◆ FinVault</div><div class="eyebrow">A BETTER WAY TO BANK · SIMULATED</div><h1>Welcome back.</h1><p>Everything you need to manage your fictional finances, beautifully in one place.</p><form method="post" action="/login">'+field('username','Username')+field('password','Password','password')+button('Sign in securely')+'</form><p>Demo: <b>alex</b> / <b>DemoPass123!</b></p><p><a href="/register">Create a demo account</a></p></div>'))
        with conn() as c:
            a=account(c,u['id']);b=balance(c,a['id']);rows=transactions(c,u['id']);csrf=s['csrf']
            if p=='/':
                recent=table([(esc(r['created_at'][:16]),esc(r['description']),'<span class="'+('positive' if r['amount']>0 else 'negative')+'">'+('+' if r['amount']>0 else '−')+money(abs(r['amount']))+'</span>') for r in rows[:6]],['Date','Activity','Amount'])
                goals=c.execute('SELECT * FROM goals WHERE user_id=?',(u['id'],)).fetchall()
                g=''.join('<div class="goal"><div><b>'+esc(x['name'])+'</b><span>'+money(x['saved'])+' / '+money(x['target'])+'</span></div><div class="track"><i style="width:'+str(min(100,100*x['saved']//x['target']))+'%"></i></div></div>' for x in goals) or '<p>No savings goals yet.</p>'
                content='<div class="eyebrow">YOUR PRIVATE FINANCIAL SPACE</div><h1>Good to see you, '+esc(u['username'].title())+'.</h1><p class="lead">Here is your financial picture at a glance.</p><div class="hero"><div><div class="eyebrow">TOTAL AVAILABLE BALANCE</div><div class="balance">'+money(b)+'</div><p>Account '+esc(a['number'])+' · '+esc(a['status'].title())+'</p><a class="btn secondary" href="/transfer">Make a transfer →</a></div><div class="card-art"><div>◆ FINVAULT <span>PREMIUM</span></div><div class="chip">▦</div><div>•••• •••• •••• '+esc(c.execute('SELECT last4 FROM cards WHERE user_id=?',(u['id'],)).fetchone()[0])+'</div><small>DEMO ACCOUNT · NOT A PAYMENT CARD</small></div></div><div class="metrics"><div><small>Transactions</small><strong>'+str(len(rows))+'</strong><span>Recorded ledger movements</span></div><div><small>Money received</small><strong>'+money(sum(max(0,r['amount']) for r in rows if r['kind']!='seed'))+'</strong><span>Excludes opening demo funds</span></div><div><small>Money sent</small><strong>'+money(sum(-min(0,r['amount']) for r in rows))+'</strong><span>Outgoing transfers</span></div></div><div class="columns">'+panel('Recent activity',recent+'<a class="small-link" href="/transactions">View all transactions →</a>')+panel('Savings goals',g+'<a class="small-link" href="/savings">Manage savings →</a>')+'</div>'
            elif p=='/transfer':content='<h1>Transfer funds</h1><p class="lead">Move fictional funds securely between FinVault accounts.</p>'+panel('New transfer','<form method="post" action="/transfer">'+token(csrf)+field('destination','Destination account number',value='FV00000004')+field('amount','Amount in BDT',type='number',extra='step="0.01" min="0.01"')+'<input type="hidden" name="key" value="'+secrets.token_hex(16)+'">'+button('Confirm transfer')+'</form><p class="hint">Try sending funds to Maya: FV00000004. Transfers cannot be reversed through this demo interface.</p>')
            elif p=='/transactions':content='<h1>Transaction history</h1><p class="lead">An auditable record of your simulated account activity.</p>'+panel('Account statement',table([(esc(r['reference']),esc(r['created_at']),esc(r['description']),money(r['amount'])) for r in rows],['Reference','Date','Description','Net amount'])+'<a class="small-link" href="/statement.csv">Download CSV statement ↓</a>')
            elif p=='/savings':
                goals=c.execute('SELECT * FROM goals WHERE user_id=?',(u['id'],)).fetchall()
                content='<h1>Savings goals</h1><p class="lead">Track personal goals using simulated savings allocations.</p>'+panel('Your goals',table([(esc(g['name']),money(g['target']),money(g['saved'])) for g in goals],['Goal','Target','Allocated']))+panel('Create a savings goal','<form action="/savings" method="post">'+token(csrf)+field('name','Goal name')+field('target','Target (BDT)','number',extra='step="0.01" min="0.01"')+button('Create goal')+'</form>')
            elif p=='/budgets':
                budgets=c.execute('SELECT * FROM budgets WHERE user_id=?',(u['id'],)).fetchall()
                content='<h1>Budget planner</h1><p class="lead">Set and organize personal spending budgets.</p>'+panel('Monthly budgets',table([(esc(x['category']),money(x['amount'])) for x in budgets],['Category','Limit']))+panel('Add budget','<form method="post" action="/budgets">'+token(csrf)+field('category','Category')+field('amount','Monthly limit (BDT)','number',extra='step="0.01" min="0.01"')+button('Save budget')+'</form>')
            elif p=='/cards':
                card=c.execute('SELECT * FROM cards WHERE user_id=?',(u['id'],)).fetchone();content='<h1>Virtual demo card</h1><p class="lead">A fictional card for learning account controls. Not usable for purchases.</p>'+panel('Your demo card','<div class="card-art standalone"><div>◆ FINVAULT <span>VIRTUAL</span></div><div class="chip">▦</div><div>•••• •••• •••• '+esc(card['last4'])+'</div><small>SIMULATION ONLY</small></div><p>Status: <b>'+('Frozen' if card['frozen'] else 'Active')+'</b></p><form method="post" action="/cards">'+token(csrf)+button('Unfreeze card' if card['frozen'] else 'Freeze card')+'</form>')
            elif p=='/admin' and u['role']=='admin':
                users=c.execute('SELECT u.id,u.username,u.role,a.number,a.status FROM users u JOIN accounts a ON a.user_id=u.id WHERE u.username != "demo_treasury" ORDER BY u.id').fetchall()
                content='<h1>Administration</h1><p class="lead">Demo account operations and access controls.</p>'+panel('Customer accounts',table([(esc(x['username']),esc(x['role']),esc(x['number']),esc(x['status']),'<form method="post" action="/admin">'+token(csrf)+'<input type="hidden" name="account_id" value="'+str(x['id'])+'">'+button('Toggle status')+'</form>') for x in users],['User','Role','Account','Status','Action']))+panel('Audit log',table([(esc(x['created_at']),esc(x['action']),esc(x['detail'] or '')) for x in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 20')],['Time','Event','Detail']))
            elif p=='/support' and u['role']=='support':content='<h1>Support workspace</h1><p class="lead">Support role is intentionally read-only in this demo.</p>'+panel('Permissions','<p>Customer financial information is restricted. Support users cannot change balances, cards, or account status.</p>')
            elif p=='/statement.csv':
                import csv,io
                out=io.StringIO();w=csv.writer(out);w.writerow(['reference','date','description','amount_BDT']);[w.writerow([r['reference'],r['created_at'],r['description'],str(Decimal(r['amount'])/100)]) for r in rows]
                return self.respond(out.getvalue(),ctype='text/csv; charset=utf-8')
            else:return self.respond(layout('Not found','<h1>Page not found</h1>',u,csrf),404)
        self.respond(layout(p.strip('/').title() or 'Dashboard',content,u,csrf))
    def do_POST(self):
        p=urlparse(self.path).path
        if int(self.headers.get('Content-Length','0'))>20000:return self.respond('Request too large',413)
        data=parse_qs(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode('utf8',errors='replace'))
        get=lambda k:data.get(k,[''])[0]
        u,s=self.session()
        try:
            if p=='/login':
                with conn() as c:row=c.execute('SELECT * FROM users WHERE username=?',(get('username').strip().lower(),)).fetchone()
                if not row or not password_ok(get('password'),row['password']):raise ValueError('Invalid username or password.')
                sid=secrets.token_urlsafe(32);SESSIONS[sid]={'uid':row['id'],'csrf':secrets.token_urlsafe(24),'expires':time.time()+8*3600}
                return self.redirect('/',f'sid={sid}; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800')
            if p=='/register':register(get('username'),get('password'));return self.redirect('/login')
            if not u or not s:return self.redirect('/login')
            if not hmac.compare_digest(get('csrf'),s['csrf']):return self.respond('CSRF validation failed',403)
            if p=='/logout':
                cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''));SESSIONS.pop(cookie['sid'].value if 'sid' in cookie else '',None)
                return self.redirect('/login','sid=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0')
            if p=='/transfer':ref,repeat=transfer(u['id'],get('destination'),get('amount'),get('key'));return self.respond(layout('Transfer complete','<h1>Transfer '+('already processed' if repeat else 'completed')+'</h1><p class="lead">Reference: '+esc(ref)+'</p><a class="btn" href="/transactions">View transactions →</a>',u,s['csrf']))
            if p in ('/savings','/budgets'):
                amount=cents(get('target') if p=='/savings' else get('amount'))
                if isinstance(amount,ValueError):raise amount
                name=get('name' if p=='/savings' else 'category').strip()
                if not 1<=len(name)<=80:raise ValueError('Name must contain 1–80 characters.')
                with conn() as c:
                    if p=='/savings':c.execute('INSERT INTO goals(user_id,name,target) VALUES(?,?,?)',(u['id'],name,amount))
                    else:c.execute('INSERT INTO budgets(user_id,category,amount) VALUES(?,?,?)',(u['id'],name,amount))
                    audit(c,u['id'],p.strip('/'),name)
                return self.redirect(p)
            if p=='/cards':
                with conn() as c:c.execute('UPDATE cards SET frozen=1-frozen WHERE user_id=?',(u['id'],));audit(c,u['id'],'card_toggle')
                return self.redirect('/cards')
            if p=='/admin' and u['role']=='admin':
                with conn() as c:
                    target=c.execute('SELECT a.id,a.status,u.username FROM accounts a JOIN users u ON a.user_id=u.id WHERE u.id=?',(get('account_id'),)).fetchone()
                    if not target or target['username']=='demo_treasury':raise ValueError('Account not found.')
                    c.execute('UPDATE accounts SET status=? WHERE id=?',('frozen' if target['status']=='active' else 'active',target['id']));audit(c,u['id'],'account_status',target['username'])
                return self.redirect('/admin')
            return self.respond('Forbidden',403)
        except (ValueError,sqlite3.IntegrityError) as e:
            return self.respond(layout('Request not completed','<h1>Unable to complete request</h1><p class="lead">'+esc(str(e))+'</p><a class="btn" href="'+('/login' if not u else '/')+'">Go back →</a>',u,s['csrf'] if s else ''),400)

def serve(port=8000):
    seed();server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'FinVault running at http://127.0.0.1:{port} (Ctrl+C to stop)');server.serve_forever()
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--seed',action='store_true');parser.add_argument('--port',type=int,default=8000);args=parser.parse_args()
    if args.seed:seed();print('Demo database ready.')
    else:serve(args.port)
