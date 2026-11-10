import os
import shutil
import sqlite3
from datetime import datetime
from functools import wraps
from io import BytesIO
from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    session,
)
import requests
from werkzeug.security import check_password_hash, generate_password_hash
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
EXCEL_PATH = os.path.join(BASE_DIR, "RELATÓRIO DE DEFINIÇÃO DE CARTOES.xlsx")


def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


load_env_file()

SUPABASE_URL = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    or ""
).strip()

# Em ambiente Serverless (Vercel), o diretório raiz é somente leitura; usamos /tmp para fallback SQLite
IS_VERCEL = bool(os.environ.get("VERCEL"))
if IS_VERCEL:
    BUNDLED_DB = os.path.join(BASE_DIR, "estacionamento.db")
    DB_PATH = "/tmp/estacionamento.db"
    if os.path.exists(BUNDLED_DB) and not os.path.exists(DB_PATH):
        try:
            shutil.copy2(BUNDLED_DB, DB_PATH)
        except Exception:
            pass
else:
    DB_PATH = os.path.join(BASE_DIR, "estacionamento.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "estacionamento-cartoes-saldo-secret-2026")

_SUPABASE_READY = None


def sb_headers(prefer=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def use_supabase():
    global _SUPABASE_READY
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    if _SUPABASE_READY is True:
        return True
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/usuarios?select=id&limit=1",
            headers=sb_headers(),
            timeout=4,
        )
        if r.status_code == 200:
            _SUPABASE_READY = True
            return True
    except Exception:
        pass
    return False


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    if not IS_VERCEL:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def format_date_cell(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    return s[:10] if len(s) >= 10 else s


def to_cents(val):
    if val is None or val == "":
        return 0
    if isinstance(val, str):
        val = val.replace("R$", "").replace(" ", "").replace(",", ".")
    return int(round(float(val) * 100))


def from_cents(cents):
    return round((cents or 0) / 100.0, 2)


def init_db():
    if use_supabase():
        try:
            from migrar_para_supabase import migrar
            migrar()
        except Exception:
            pass
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            nome TEXT NOT NULL,
            senha_hash TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'operadora',
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cartoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero INTEGER NOT NULL,
            grupo INTEGER NOT NULL DEFAULT 1,
            saldo_inicial_centavos INTEGER NOT NULL,
            saldo_centavos INTEGER NOT NULL,
            data_inicio TEXT,
            data_fim TEXT,
            nome TEXT,
            observacao TEXT DEFAULT '',
            atualizado_em TEXT
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cartoes_numero ON cartoes(numero)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cartoes_nome ON cartoes(nome)")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movimentacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cartao_id INTEGER NOT NULL,
            cartao_numero INTEGER NOT NULL,
            usuario_id INTEGER NOT NULL,
            usuario_nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            valor_estadia_centavos INTEGER NOT NULL,
            valor_descontado_centavos INTEGER NOT NULL,
            valor_complemento_caixa_centavos INTEGER NOT NULL DEFAULT 0,
            saldo_anterior_centavos INTEGER NOT NULL,
            saldo_posterior_centavos INTEGER NOT NULL,
            observacao TEXT DEFAULT '',
            estornado INTEGER NOT NULL DEFAULT 0,
            estornado_por_nome TEXT,
            estornado_em TEXT,
            criado_em TEXT NOT NULL,
            FOREIGN KEY (cartao_id) REFERENCES cartoes(id),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mov_cartao_id ON movimentacoes(cartao_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mov_cartao_num ON movimentacoes(cartao_numero)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mov_criado_em ON movimentacoes(criado_em)")

    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        default_users = [
            ("admin", "Administrador", generate_password_hash("admin123"), "admin", 1, now),
            ("caixa1", "Operadora Caixa 01", generate_password_hash("1234"), "operadora", 1, now),
            ("caixa2", "Operadora Caixa 02", generate_password_hash("1234"), "operadora", 1, now),
        ]
        cur.executemany(
            """
            INSERT INTO usuarios (username, nome, senha_hash, perfil, ativo, criado_em)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_users,
        )
        conn.commit()

    cur.execute("SELECT COUNT(*) FROM cartoes")
    if cur.fetchone()[0] == 0 and os.path.exists(EXCEL_PATH):
        import_excel_to_db(conn, EXCEL_PATH)

    conn.close()


def import_excel_to_db(conn, filepath):
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    next(rows, None)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = []
    for row in rows:
        if not row or row[0] is None:
            continue
        numero = int(row[0])
        grupo = int(row[1]) if row[1] is not None else 1
        saldo_cents = to_cents(row[2])
        dt_inicio = format_date_cell(row[3])
        dt_fim = format_date_cell(row[4])
        nome = str(row[5]).strip() if row[5] is not None else ""
        batch.append((numero, grupo, saldo_cents, saldo_cents, dt_inicio, dt_fim, nome, "", now))

    if batch:
        conn.executemany(
            """
            INSERT INTO cartoes (
                numero, grupo, saldo_inicial_centavos, saldo_centavos,
                data_inicio, data_fim, nome, observacao, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            batch,
        )
        conn.commit()
    wb.close()
    return len(batch)


# Decorators de Autenticação
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Não autenticado. Faça login para continuar."}), 401
        return f(*args, **kwargs)

    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Não autenticado."}), 401
        if session.get("perfil") != "admin":
            return jsonify({"error": "Acesso restrito a administradores."}), 403
        return f(*args, **kwargs)

    return decorated


def serialize_cartao(row):
    nome = row.get("nome") if isinstance(row, dict) else row["nome"]
    nome_str = nome or ""
    return {
        "id": row["id"],
        "numero": row["numero"],
        "grupo": row["grupo"],
        "saldo_inicial": from_cents(row["saldo_inicial_centavos"]),
        "saldo": from_cents(row["saldo_centavos"]),
        "saldo_centavos": row["saldo_centavos"],
        "data_inicio": row["data_inicio"] or "",
        "data_fim": row["data_fim"] or "",
        "nome": nome_str,
        "observacao": row["observacao"] or "",
        "atualizado_em": row["atualizado_em"] or "",
        "precisa_atualizar_nome": (not nome_str) or (nome_str.strip().upper() == "ATUALIZAÇÃO DE CADASTRO"),
    }


def serialize_movimentacao(row):
    return {
        "id": row["id"],
        "cartao_id": row["cartao_id"],
        "cartao_numero": row["cartao_numero"],
        "usuario_id": row["usuario_id"],
        "usuario_nome": row["usuario_nome"],
        "tipo": row["tipo"],
        "valor_estadia": from_cents(row["valor_estadia_centavos"]),
        "valor_descontado": from_cents(row["valor_descontado_centavos"]),
        "valor_complemento_caixa": from_cents(row["valor_complemento_caixa_centavos"]),
        "saldo_anterior": from_cents(row["saldo_anterior_centavos"]),
        "saldo_posterior": from_cents(row["saldo_posterior_centavos"]),
        "observacao": row["observacao"] or "",
        "estornado": bool(row["estornado"]),
        "estornado_por_nome": row["estornado_por_nome"] or "",
        "estornado_em": row["estornado_em"] or "",
        "criado_em": row["criado_em"],
    }


@app.before_request
def ensure_initialized():
    if not getattr(app, "_db_initialized", False):
        init_db()
        app._db_initialized = True


@app.route("/")
def index():
    return render_template("index.html")


# ==================== AUTENTICAÇÃO ====================
@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Informe o usuário e a senha."}), 400

    if use_supabase():
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers=sb_headers(),
            params={"username": f"eq.{username}", "ativo": "eq.1", "limit": "1"},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        user = rows[0] if rows else None
    else:
        conn = get_db()
        user = conn.execute(
            "SELECT * FROM usuarios WHERE LOWER(username) = ? AND ativo = 1", (username,)
        ).fetchone()
        conn.close()

    if not user or not check_password_hash(user["senha_hash"], password):
        return jsonify({"error": "Usuário ou senha inválidos."}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["nome"] = user["nome"]
    session["perfil"] = user["perfil"]

    return jsonify(
        {
            "user": {
                "id": user["id"],
                "username": user["username"],
                "nome": user["nome"],
                "perfil": user["perfil"],
            }
        }
    )


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def api_me():
    if "user_id" not in session:
        if use_supabase():
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/usuarios",
                headers=sb_headers(),
                params={
                    "select": "username,nome,perfil",
                    "ativo": "eq.1",
                    "order": "perfil.desc,nome.asc",
                },
                timeout=10,
            )
            users = r.json() if r.status_code == 200 else []
        else:
            conn = get_db()
            rows = conn.execute(
                "SELECT username, nome, perfil FROM usuarios WHERE ativo = 1 ORDER BY perfil DESC, nome ASC"
            ).fetchall()
            conn.close()
            users = [dict(u) for u in rows]

        return jsonify(
            {
                "authenticated": False,
                "available_users": users,
                "storage_mode": "supabase" if use_supabase() else "sqlite",
            }
        )
    return jsonify(
        {
            "authenticated": True,
            "storage_mode": "supabase" if use_supabase() else "sqlite",
            "user": {
                "id": session["user_id"],
                "username": session["username"],
                "nome": session["nome"],
                "perfil": session["perfil"],
            },
        }
    )


# ==================== CONSULTA E OPERAÇÃO DE CARTÕES ====================
@app.route("/api/cartoes/busca", methods=["GET"])
@login_required
def api_buscar_cartoes():
    termo = (request.args.get("q") or "").strip()
    filtro_saldo = request.args.get("saldo", "todos")
    limit = min(int(request.args.get("limit", 30)), 100)

    if use_supabase():
        params = {"order": "numero.asc", "limit": str(limit)}
        if filtro_saldo == "com_saldo":
            params["saldo_centavos"] = "gt.0"
        elif filtro_saldo == "zerados":
            params["saldo_centavos"] = "eq.0"

        if termo:
            if termo.isdigit():
                params["numero"] = f"eq.{int(termo)}"
                r_exact = requests.get(
                    f"{SUPABASE_URL}/rest/v1/cartoes",
                    headers=sb_headers(),
                    params=params,
                    timeout=10,
                )
                exact_rows = r_exact.json() if r_exact.status_code == 200 else []
                del params["numero"]
                params["numero"] = f"gte.{int(termo)}"
                r_range = requests.get(
                    f"{SUPABASE_URL}/rest/v1/cartoes",
                    headers=sb_headers(),
                    params=params,
                    timeout=10,
                )
                range_rows = r_range.json() if r_range.status_code == 200 else []
                seen = {x["id"] for x in exact_rows}
                rows = exact_rows + [x for x in range_rows if x["id"] not in seen]
                rows = rows[:limit]
            else:
                params["nome"] = f"ilike.*{termo}*"
                r = requests.get(
                    f"{SUPABASE_URL}/rest/v1/cartoes",
                    headers=sb_headers(),
                    params=params,
                    timeout=10,
                )
                rows = r.json() if r.status_code == 200 else []
        else:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/cartoes",
                headers=sb_headers(),
                params=params,
                timeout=10,
            )
            rows = r.json() if r.status_code == 200 else []

        return jsonify({"cartoes": [serialize_cartao(r) for r in rows]})

    conn = get_db()
    params = []
    where_clauses = []

    if termo:
        if termo.isdigit():
            where_clauses.append("(CAST(numero AS TEXT) LIKE ? OR UPPER(nome) LIKE ?)")
            params.extend([f"{termo}%", f"%{termo.upper()}%"])
        else:
            where_clauses.append("(UPPER(nome) LIKE ? OR UPPER(observacao) LIKE ?)")
            params.extend([f"%{termo.upper()}%", f"%{termo.upper()}%"])

    if filtro_saldo == "com_saldo":
        where_clauses.append("saldo_centavos > 0")
    elif filtro_saldo == "zerados":
        where_clauses.append("saldo_centavos = 0")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    if termo.isdigit():
        order_sql = "ORDER BY CASE WHEN numero = ? THEN 0 ELSE 1 END, numero ASC"
        params.append(int(termo))
    else:
        order_sql = "ORDER BY numero ASC"

    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM cartoes {where_sql} {order_sql} LIMIT ?", params
    ).fetchall()
    conn.close()

    return jsonify({"cartoes": [serialize_cartao(r) for r in rows]})


@app.route("/api/cartoes/numero/<int:numero>", methods=["GET"])
@login_required
def api_cartao_por_numero(numero):
    if use_supabase():
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers(),
            params={"numero": f"eq.{numero}", "order": "saldo_centavos.desc,id.asc"},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return jsonify({"error": f"Cartão nº {numero} não encontrado na base de dados."}), 404

        cartoes = [serialize_cartao(x) for x in rows]
        cartao_principal = cartoes[0]
        rm = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params={
                "cartao_id": f"eq.{cartao_principal['id']}",
                "order": "id.desc",
                "limit": "50",
            },
            timeout=10,
        )
        movs = rm.json() if rm.status_code == 200 else []
        return jsonify(
            {
                "cartao": cartao_principal,
                "duplicados": cartoes if len(cartoes) > 1 else [],
                "historico": [serialize_movimentacao(m) for m in movs],
            }
        )

    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM cartoes WHERE numero = ? ORDER BY saldo_centavos DESC, id ASC",
        (numero,),
    ).fetchall()

    if not rows:
        conn.close()
        return jsonify({"error": f"Cartão nº {numero} não encontrado na base de dados."}), 404

    cartoes = [serialize_cartao(r) for r in rows]
    cartao_principal = cartoes[0]
    movs = conn.execute(
        """
        SELECT * FROM movimentacoes
        WHERE cartao_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (cartao_principal["id"],),
    ).fetchall()
    conn.close()

    return jsonify(
        {
            "cartao": cartao_principal,
            "duplicados": cartoes if len(cartoes) > 1 else [],
            "historico": [serialize_movimentacao(m) for m in movs],
        }
    )


@app.route("/api/cartoes/<int:cartao_id>", methods=["GET"])
@login_required
def api_cartao_por_id(cartao_id):
    if use_supabase():
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers(),
            params={"id": f"eq.{cartao_id}", "limit": "1"},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        if not rows:
            return jsonify({"error": "Cartão não encontrado."}), 404
        row = rows[0]

        r_dups = requests.get(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers(),
            params={"numero": f"eq.{row['numero']}", "order": "saldo_centavos.desc,id.asc"},
            timeout=10,
        )
        dups = r_dups.json() if r_dups.status_code == 200 else []

        r_movs = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params={"cartao_id": f"eq.{cartao_id}", "order": "id.desc", "limit": "50"},
            timeout=10,
        )
        movs = r_movs.json() if r_movs.status_code == 200 else []

        return jsonify(
            {
                "cartao": serialize_cartao(row),
                "duplicados": [serialize_cartao(d) for d in dups] if len(dups) > 1 else [],
                "historico": [serialize_movimentacao(m) for m in movs],
            }
        )

    conn = get_db()
    row = conn.execute("SELECT * FROM cartoes WHERE id = ?", (cartao_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Cartão não encontrado."}), 404

    dups = conn.execute(
        "SELECT * FROM cartoes WHERE numero = ? ORDER BY saldo_centavos DESC, id ASC",
        (row["numero"],),
    ).fetchall()
    movs = conn.execute(
        """
        SELECT * FROM movimentacoes
        WHERE cartao_id = ?
        ORDER BY id DESC
        LIMIT 50
        """,
        (cartao_id,),
    ).fetchall()
    conn.close()

    return jsonify(
        {
            "cartao": serialize_cartao(row),
            "duplicados": [serialize_cartao(d) for d in dups] if len(dups) > 1 else [],
            "historico": [serialize_movimentacao(m) for m in movs],
        }
    )


@app.route("/api/cartoes/<int:cartao_id>/cadastro", methods=["PUT"])
@login_required
def api_atualizar_cadastro_cartao(cartao_id):
    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    observacao = (data.get("observacao") or "").strip()

    if not nome:
        return jsonify({"error": "O nome do cliente não pode ficar vazio."}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if use_supabase():
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/cartoes?id=eq.{cartao_id}",
            headers=sb_headers("return=representation"),
            json={"nome": nome, "observacao": observacao, "atualizado_em": now},
            timeout=10,
        )
        rows = r.json() if r.status_code in (200, 201) else []
        if not rows:
            return jsonify({"error": "Cartão não encontrado."}), 404
        return jsonify(
            {
                "message": "Cadastro do cartão atualizado com sucesso!",
                "cartao": serialize_cartao(rows[0]),
            }
        )

    conn = get_db()
    row = conn.execute("SELECT * FROM cartoes WHERE id = ?", (cartao_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Cartão não encontrado."}), 404

    conn.execute(
        "UPDATE cartoes SET nome = ?, observacao = ?, atualizado_em = ? WHERE id = ?",
        (nome, observacao, now, cartao_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM cartoes WHERE id = ?", (cartao_id,)).fetchone()
    conn.close()

    return jsonify(
        {
            "message": "Cadastro do cartão atualizado com sucesso!",
            "cartao": serialize_cartao(updated),
        }
    )


@app.route("/api/cartoes/<int:cartao_id>/descontar", methods=["POST"])
@login_required
def api_descontar_saldo(cartao_id):
    data = request.get_json(silent=True) or {}
    valor_raw = data.get("valor_estadia")
    observacao = (data.get("observacao") or "").strip()
    nome_cliente = (data.get("nome_cliente") or "").strip()

    try:
        valor_estadia_centavos = to_cents(valor_raw)
    except (ValueError, TypeError):
        return jsonify({"error": "Valor da estadia inválido."}), 400

    if valor_estadia_centavos <= 0:
        return jsonify({"error": "Informe um valor de estadia maior que R$ 0,00."}), 400

    if use_supabase():
        rc = requests.get(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers(),
            params={"id": f"eq.{cartao_id}", "limit": "1"},
            timeout=10,
        )
        rows = rc.json() if rc.status_code == 200 else []
        if not rows:
            return jsonify({"error": "Cartão não encontrado."}), 404
        cartao = rows[0]
        saldo_atual = cartao["saldo_centavos"]
        if saldo_atual <= 0:
            return jsonify(
                {"error": f"O cartão nº {cartao['numero']} já está com saldo ZERADO (R$ 0,00)."}
            ), 400

        if valor_estadia_centavos <= saldo_atual:
            tipo = "BAIXA_TOTAL"
            valor_descontado = valor_estadia_centavos
            valor_complemento_caixa = 0
            novo_saldo = saldo_atual - valor_estadia_centavos
        else:
            tipo = "BAIXA_PARCIAL"
            valor_descontado = saldo_atual
            valor_complemento_caixa = valor_estadia_centavos - saldo_atual
            novo_saldo = 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        novo_nome = nome_cliente if nome_cliente else cartao["nome"]

        ru = requests.patch(
            f"{SUPABASE_URL}/rest/v1/cartoes?id=eq.{cartao_id}",
            headers=sb_headers("return=representation"),
            json={"saldo_centavos": novo_saldo, "nome": novo_nome, "atualizado_em": now},
            timeout=10,
        )
        cartao_atualizado = ru.json()[0]

        rm = requests.post(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers("return=representation"),
            json={
                "cartao_id": cartao_id,
                "cartao_numero": cartao["numero"],
                "usuario_id": session["user_id"],
                "usuario_nome": session["nome"],
                "tipo": tipo,
                "valor_estadia_centavos": valor_estadia_centavos,
                "valor_descontado_centavos": valor_descontado,
                "valor_complemento_caixa_centavos": valor_complemento_caixa,
                "saldo_anterior_centavos": saldo_atual,
                "saldo_posterior_centavos": novo_saldo,
                "observacao": observacao,
                "estornado": 0,
                "criado_em": now,
            },
            timeout=10,
        )
        mov_criada = rm.json()[0]

        rh = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params={"cartao_id": f"eq.{cartao_id}", "order": "id.desc", "limit": "50"},
            timeout=10,
        )
        historico = rh.json() if rh.status_code == 200 else []

        return jsonify(
            {
                "message": "Baixa realizada com sucesso!",
                "cartao": serialize_cartao(cartao_atualizado),
                "movimentacao": serialize_movimentacao(mov_criada),
                "historico": [serialize_movimentacao(m) for m in historico],
            }
        )

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cartao = conn.execute("SELECT * FROM cartoes WHERE id = ?", (cartao_id,)).fetchone()
        if not cartao:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Cartão não encontrado."}), 404

        saldo_atual = cartao["saldo_centavos"]
        if saldo_atual <= 0:
            conn.rollback()
            conn.close()
            return jsonify(
                {"error": f"O cartão nº {cartao['numero']} já está com saldo ZERADO (R$ 0,00)."}
            ), 400

        if valor_estadia_centavos <= saldo_atual:
            tipo = "BAIXA_TOTAL"
            valor_descontado = valor_estadia_centavos
            valor_complemento_caixa = 0
            novo_saldo = saldo_atual - valor_estadia_centavos
        else:
            tipo = "BAIXA_PARCIAL"
            valor_descontado = saldo_atual
            valor_complemento_caixa = valor_estadia_centavos - saldo_atual
            novo_saldo = 0

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        novo_nome = nome_cliente if nome_cliente else cartao["nome"]

        conn.execute(
            """
            UPDATE cartoes
            SET saldo_centavos = ?, nome = ?, atualizado_em = ?
            WHERE id = ?
            """,
            (novo_saldo, novo_nome, now, cartao_id),
        )

        cur = conn.execute(
            """
            INSERT INTO movimentacoes (
                cartao_id, cartao_numero, usuario_id, usuario_nome, tipo,
                valor_estadia_centavos, valor_descontado_centavos,
                valor_complemento_caixa_centavos, saldo_anterior_centavos,
                saldo_posterior_centavos, observacao, criado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cartao_id,
                cartao["numero"],
                session["user_id"],
                session["nome"],
                tipo,
                valor_estadia_centavos,
                valor_descontado,
                valor_complemento_caixa,
                saldo_atual,
                novo_saldo,
                observacao,
                now,
            ),
        )
        mov_id = cur.lastrowid
        conn.commit()

        cartao_atualizado = conn.execute(
            "SELECT * FROM cartoes WHERE id = ?", (cartao_id,)
        ).fetchone()
        mov_criada = conn.execute(
            "SELECT * FROM movimentacoes WHERE id = ?", (mov_id,)
        ).fetchone()
        historico = conn.execute(
            "SELECT * FROM movimentacoes WHERE cartao_id = ? ORDER BY id DESC LIMIT 50",
            (cartao_id,),
        ).fetchall()
        conn.close()

        return jsonify(
            {
                "message": "Baixa realizada com sucesso!",
                "cartao": serialize_cartao(cartao_atualizado),
                "movimentacao": serialize_movimentacao(mov_criada),
                "historico": [serialize_movimentacao(m) for m in historico],
            }
        )
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"Erro ao processar baixa: {str(e)}"}), 500


@app.route("/api/movimentacoes/<int:mov_id>/estornar", methods=["POST"])
@login_required
def api_estornar_movimentacao(mov_id):
    data = request.get_json(silent=True) or {}
    motivo = (data.get("motivo") or "Estorno de lançamento").strip()
    hoje = datetime.now().strftime("%Y-%m-%d")

    if use_supabase():
        rm = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params={"id": f"eq.{mov_id}", "limit": "1"},
            timeout=10,
        )
        mrows = rm.json() if rm.status_code == 200 else []
        if not mrows:
            return jsonify({"error": "Movimentação não encontrada."}), 404
        mov = mrows[0]

        if mov["estornado"]:
            return jsonify({"error": "Esta movimentação já foi estornada."}), 400
        if mov["tipo"] == "ESTORNO":
            return jsonify({"error": "Não é possível estornar um registro de estorno."}), 400
        if session.get("perfil") != "admin" and not mov["criado_em"].startswith(hoje):
            return jsonify(
                {"error": "Apenas o Administrador pode estornar baixas de dias anteriores."}
            ), 403

        rc = requests.get(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers(),
            params={"id": f"eq.{mov['cartao_id']}", "limit": "1"},
            timeout=10,
        )
        crows = rc.json() if rc.status_code == 200 else []
        if not crows:
            return jsonify({"error": "Cartão vinculado não encontrado."}), 404
        cartao = crows[0]

        saldo_atual = cartao["saldo_centavos"]
        valor_devolvido = mov["valor_descontado_centavos"]
        novo_saldo = saldo_atual + valor_devolvido
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        ru = requests.patch(
            f"{SUPABASE_URL}/rest/v1/cartoes?id=eq.{cartao['id']}",
            headers=sb_headers("return=representation"),
            json={"saldo_centavos": novo_saldo, "atualizado_em": now},
            timeout=10,
        )
        cartao_atualizado = ru.json()[0]

        requests.patch(
            f"{SUPABASE_URL}/rest/v1/movimentacoes?id=eq.{mov_id}",
            headers=sb_headers("return=minimal"),
            json={
                "estornado": 1,
                "estornado_por_nome": session["nome"],
                "estornado_em": now,
            },
            timeout=10,
        )

        requests.post(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers("return=minimal"),
            json={
                "cartao_id": cartao["id"],
                "cartao_numero": cartao["numero"],
                "usuario_id": session["user_id"],
                "usuario_nome": session["nome"],
                "tipo": "ESTORNO",
                "valor_estadia_centavos": 0,
                "valor_descontado_centavos": valor_devolvido,
                "valor_complemento_caixa_centavos": 0,
                "saldo_anterior_centavos": saldo_atual,
                "saldo_posterior_centavos": novo_saldo,
                "observacao": f"Estorno ref. baixa #{mov_id}: {motivo}",
                "estornado": 0,
                "criado_em": now,
            },
            timeout=10,
        )

        rh = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params={"cartao_id": f"eq.{cartao['id']}", "order": "id.desc", "limit": "50"},
            timeout=10,
        )
        historico = rh.json() if rh.status_code == 200 else []

        return jsonify(
            {
                "message": f"Estorno realizado! R$ {from_cents(valor_devolvido):.2f} devolvido ao cartão nº {cartao['numero']}.",
                "cartao": serialize_cartao(cartao_atualizado),
                "historico": [serialize_movimentacao(m) for m in historico],
            }
        )

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        mov = conn.execute("SELECT * FROM movimentacoes WHERE id = ?", (mov_id,)).fetchone()
        if not mov:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Movimentação não encontrada."}), 404

        if mov["estornado"]:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Esta movimentação já foi estornada."}), 400

        if mov["tipo"] == "ESTORNO":
            conn.rollback()
            conn.close()
            return jsonify({"error": "Não é possível estornar um registro de estorno."}), 400

        if session.get("perfil") != "admin" and not mov["criado_em"].startswith(hoje):
            conn.rollback()
            conn.close()
            return jsonify(
                {"error": "Apenas o Administrador pode estornar baixas de dias anteriores."}
            ), 403

        cartao = conn.execute(
            "SELECT * FROM cartoes WHERE id = ?", (mov["cartao_id"],)
        ).fetchone()
        if not cartao:
            conn.rollback()
            conn.close()
            return jsonify({"error": "Cartão vinculado não encontrado."}), 404

        saldo_atual = cartao["saldo_centavos"]
        valor_devolvido = mov["valor_descontado_centavos"]
        novo_saldo = saldo_atual + valor_devolvido
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn.execute(
            "UPDATE cartoes SET saldo_centavos = ?, atualizado_em = ? WHERE id = ?",
            (novo_saldo, now, cartao["id"]),
        )

        conn.execute(
            """
            UPDATE movimentacoes
            SET estornado = 1, estornado_por_nome = ?, estornado_em = ?
            WHERE id = ?
            """,
            (session["nome"], now, mov_id),
        )

        conn.execute(
            """
            INSERT INTO movimentacoes (
                cartao_id, cartao_numero, usuario_id, usuario_nome, tipo,
                valor_estadia_centavos, valor_descontado_centavos,
                valor_complemento_caixa_centavos, saldo_anterior_centavos,
                saldo_posterior_centavos, observacao, criado_em
            ) VALUES (?, ?, ?, ?, 'ESTORNO', 0, ?, 0, ?, ?, ?, ?)
            """,
            (
                cartao["id"],
                cartao["numero"],
                session["user_id"],
                session["nome"],
                valor_devolvido,
                saldo_atual,
                novo_saldo,
                f"Estorno ref. baixa #{mov_id}: {motivo}",
                now,
            ),
        )
        conn.commit()

        cartao_atualizado = conn.execute(
            "SELECT * FROM cartoes WHERE id = ?", (cartao["id"],)
        ).fetchone()
        historico = conn.execute(
            "SELECT * FROM movimentacoes WHERE cartao_id = ? ORDER BY id DESC LIMIT 50",
            (cartao["id"],),
        ).fetchall()
        conn.close()

        return jsonify(
            {
                "message": f"Estorno realizado! R$ {from_cents(valor_devolvido):.2f} devolvido ao cartão nº {cartao['numero']}.",
                "cartao": serialize_cartao(cartao_atualizado),
                "historico": [serialize_movimentacao(m) for m in historico],
            }
        )
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": f"Erro ao estornar: {str(e)}"}), 500


# ==================== DASHBOARD, HISTÓRICO GERAL E EXPORTAÇÃO ====================
@app.route("/api/resumo", methods=["GET"])
@login_required
def api_resumo():
    hoje = datetime.now().strftime("%Y-%m-%d")

    if use_supabase():
        # Tenta usar a função RPC obter_resumo_caixa se disponível
        r_rpc = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/obter_resumo_caixa",
            headers=sb_headers(),
            json={"p_hoje": hoje, "p_usuario_id": session["user_id"]},
            timeout=10,
        )
        if r_rpc.status_code == 200 and isinstance(r_rpc.json(), dict):
            return jsonify(r_rpc.json())

    conn = get_db()
    stats_cartoes = conn.execute(
        """
        SELECT
            COUNT(*) AS total_cartoes,
            SUM(CASE WHEN saldo_centavos > 0 THEN 1 ELSE 0 END) AS cartoes_com_saldo,
            SUM(CASE WHEN saldo_centavos = 0 THEN 1 ELSE 0 END) AS cartoes_zerados,
            COALESCE(SUM(saldo_inicial_centavos), 0) AS saldo_inicial_total,
            COALESCE(SUM(saldo_centavos), 0) AS saldo_atual_total
        FROM cartoes
        """
    ).fetchone()

    stats_hoje = conn.execute(
        """
        SELECT
            COUNT(*) AS atendimentos_hoje,
            COALESCE(SUM(valor_descontado_centavos), 0) AS descontado_hoje,
            COALESCE(SUM(valor_complemento_caixa_centavos), 0) AS complemento_caixa_hoje
        FROM movimentacoes
        WHERE criado_em LIKE ?
          AND tipo IN ('BAIXA_TOTAL', 'BAIXA_PARCIAL')
          AND estornado = 0
        """,
        (f"{hoje}%",),
    ).fetchone()

    stats_operadora_hoje = conn.execute(
        """
        SELECT
            COUNT(*) AS meus_atendimentos_hoje,
            COALESCE(SUM(valor_descontado_centavos), 0) AS meu_descontado_hoje,
            COALESCE(SUM(valor_complemento_caixa_centavos), 0) AS meu_complemento_hoje
        FROM movimentacoes
        WHERE criado_em LIKE ?
          AND usuario_id = ?
          AND tipo IN ('BAIXA_TOTAL', 'BAIXA_PARCIAL')
          AND estornado = 0
        """,
        (f"{hoje}%", session["user_id"]),
    ).fetchone()

    conn.close()

    saldo_inicial = stats_cartoes["saldo_inicial_total"]
    saldo_atual = stats_cartoes["saldo_atual_total"]

    return jsonify(
        {
            "total_cartoes": stats_cartoes["total_cartoes"],
            "cartoes_com_saldo": stats_cartoes["cartoes_com_saldo"] or 0,
            "cartoes_zerados": stats_cartoes["cartoes_zerados"] or 0,
            "saldo_inicial_total": from_cents(saldo_inicial),
            "saldo_atual_total": from_cents(saldo_atual),
            "total_ja_utilizado": from_cents(saldo_inicial - saldo_atual),
            "atendimentos_hoje": stats_hoje["atendimentos_hoje"] or 0,
            "descontado_hoje": from_cents(stats_hoje["descontado_hoje"]),
            "complemento_caixa_hoje": from_cents(stats_hoje["complemento_caixa_hoje"]),
            "meus_atendimentos_hoje": stats_operadora_hoje["meus_atendimentos_hoje"] or 0,
            "meu_descontado_hoje": from_cents(stats_operadora_hoje["meu_descontado_hoje"]),
            "meu_complemento_hoje": from_cents(stats_operadora_hoje["meu_complemento_hoje"]),
        }
    )


@app.route("/api/movimentacoes", methods=["GET"])
@login_required
def api_listar_movimentacoes():
    data_inicio = (request.args.get("data_inicio") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()
    cartao_numero = (request.args.get("cartao") or "").strip()
    usuario_id = (request.args.get("usuario_id") or "").strip()
    limit = min(int(request.args.get("limit", 100)), 500)

    if use_supabase():
        params = [("order", "id.desc"), ("limit", str(limit))]
        if data_inicio:
            params.append(("criado_em", f"gte.{data_inicio} 00:00:00"))
        if data_fim:
            params.append(("criado_em", f"lte.{data_fim} 23:59:59"))
        if cartao_numero and cartao_numero.isdigit():
            params.append(("cartao_numero", f"eq.{int(cartao_numero)}"))
        if usuario_id and usuario_id.isdigit():
            params.append(("usuario_id", f"eq.{int(usuario_id)}"))

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params=params,
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        return jsonify({"movimentacoes": [serialize_movimentacao(x) for x in rows]})

    where = []
    params = []

    if data_inicio:
        where.append("criado_em >= ?")
        params.append(f"{data_inicio} 00:00:00")
    if data_fim:
        where.append("criado_em <= ?")
        params.append(f"{data_fim} 23:59:59")
    if cartao_numero and cartao_numero.isdigit():
        where.append("cartao_numero = ?")
        params.append(int(cartao_numero))
    if usuario_id and usuario_id.isdigit():
        where.append("usuario_id = ?")
        params.append(int(usuario_id))

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)

    conn = get_db()
    rows = conn.execute(
        f"SELECT * FROM movimentacoes {where_sql} ORDER BY id DESC LIMIT ?", params
    ).fetchall()
    conn.close()

    return jsonify({"movimentacoes": [serialize_movimentacao(r) for r in rows]})


# ==================== ADMIN: USUÁRIOS E CARTÕES ====================
@app.route("/api/usuarios", methods=["GET"])
@login_required
def api_listar_usuarios():
    if use_supabase():
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers=sb_headers(),
            params={"select": "id,username,nome,perfil,ativo,criado_em", "order": "id.asc"},
            timeout=10,
        )
        rows = r.json() if r.status_code == 200 else []
        return jsonify({"usuarios": rows})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, nome, perfil, ativo, criado_em FROM usuarios ORDER BY id ASC"
    ).fetchall()
    conn.close()
    return jsonify({"usuarios": [dict(r) for r in rows]})


@app.route("/api/usuarios", methods=["POST"])
@admin_required
def api_criar_usuario():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    nome = (data.get("nome") or "").strip()
    senha = (data.get("senha") or "").strip()
    perfil = data.get("perfil") if data.get("perfil") in ("operadora", "admin") else "operadora"

    if not username or not nome or not senha:
        return jsonify({"error": "Preencha usuário (login), nome e senha."}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if use_supabase():
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers=sb_headers("return=representation"),
            json={
                "username": username,
                "nome": nome,
                "senha_hash": generate_password_hash(senha),
                "perfil": perfil,
                "ativo": 1,
                "criado_em": now,
            },
            timeout=10,
        )
        if r.status_code not in (200, 201):
            return jsonify({"error": f"O login '{username}' já está em uso ou ocorreu um erro."}), 400
        return jsonify({"message": f"Operadora '{nome}' cadastrada com sucesso!"})

    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO usuarios (username, nome, senha_hash, perfil, ativo, criado_em)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (username, nome, generate_password_hash(senha), perfil, now),
        )
        conn.commit()
        conn.close()
        return jsonify({"message": f"Operadora '{nome}' cadastrada com sucesso!"})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": f"O login '{username}' já está em uso."}), 400


@app.route("/api/cartoes/novo", methods=["POST"])
@admin_required
def api_criar_cartao():
    data = request.get_json(silent=True) or {}
    numero = data.get("numero")
    grupo = data.get("grupo", 1)
    saldo = data.get("saldo", 0)
    nome = (data.get("nome") or "").strip()
    data_inicio = (data.get("data_inicio") or datetime.now().strftime("%Y-%m-%d")).strip()
    data_fim = (data.get("data_fim") or "").strip()

    if not numero or not str(numero).isdigit():
        return jsonify({"error": "Informe um número de cartão válido."}), 400

    saldo_cents = to_cents(saldo)
    if saldo_cents < 0:
        return jsonify({"error": "O saldo não pode ser negativo."}), 400

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if use_supabase():
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers("return=representation"),
            json={
                "numero": int(numero),
                "grupo": int(grupo or 1),
                "saldo_inicial_centavos": saldo_cents,
                "saldo_centavos": saldo_cents,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "nome": nome,
                "observacao": "",
                "atualizado_em": now,
            },
            timeout=10,
        )
        row = r.json()[0]
        return jsonify(
            {
                "message": f"Cartão nº {numero} cadastrado com sucesso!",
                "cartao": serialize_cartao(row),
            }
        )

    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO cartoes (
            numero, grupo, saldo_inicial_centavos, saldo_centavos,
            data_inicio, data_fim, nome, observacao, atualizado_em
        ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?)
        """,
        (int(numero), int(grupo or 1), saldo_cents, saldo_cents, data_inicio, data_fim, nome, now),
    )
    cartao_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM cartoes WHERE id = ?", (cartao_id,)).fetchone()
    conn.close()

    return jsonify(
        {
            "message": f"Cartão nº {numero} cadastrado com sucesso!",
            "cartao": serialize_cartao(row),
        }
    )


# ==================== EXPORTAÇÃO EXCEL ====================
@app.route("/api/exportar/saldos", methods=["GET"])
@login_required
def api_exportar_saldos():
    if use_supabase():
        rows = []
        offset = 0
        while True:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/cartoes",
                headers=sb_headers(),
                params={"order": "numero.asc", "limit": "1000", "offset": str(offset)},
                timeout=15,
            )
            batch = r.json() if r.status_code == 200 else []
            rows.extend(batch)
            if len(batch) < 1000:
                break
            offset += 1000
    else:
        conn = get_db()
        rows = conn.execute("SELECT * FROM cartoes ORDER BY numero ASC").fetchall()
        conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Saldos de Cartões"

    headers = ["NÚMERO", "GRUPO", "SALDO INICIAL (R$)", "SALDO ATUAL (R$)", "INÍCIO", "FIM", "NOME", "ÚLTIMA ATUALIZAÇÃO"]
    ws.append(headers)

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center")

    for r in rows:
        ws.append(
            [
                r["numero"],
                r["grupo"],
                from_cents(r["saldo_inicial_centavos"]),
                from_cents(r["saldo_centavos"]),
                r["data_inicio"],
                r["data_fim"],
                r["nome"],
                r["atualizado_em"],
            ]
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"RELATORIO_SALDOS_ATUALIZADO_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/api/exportar/movimentacoes", methods=["GET"])
@login_required
def api_exportar_movimentacoes():
    data_inicio = (request.args.get("data_inicio") or "").strip()
    data_fim = (request.args.get("data_fim") or "").strip()

    if use_supabase():
        params = [("order", "id.desc"), ("limit", "5000")]
        if data_inicio:
            params.append(("criado_em", f"gte.{data_inicio} 00:00:00"))
        if data_fim:
            params.append(("criado_em", f"lte.{data_fim} 23:59:59"))
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/movimentacoes",
            headers=sb_headers(),
            params=params,
            timeout=15,
        )
        rows = r.json() if r.status_code == 200 else []
    else:
        where = []
        params = []
        if data_inicio:
            where.append("criado_em >= ?")
            params.append(f"{data_inicio} 00:00:00")
        if data_fim:
            where.append("criado_em <= ?")
            params.append(f"{data_fim} 23:59:59")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        conn = get_db()
        rows = conn.execute(
            f"SELECT * FROM movimentacoes {where_sql} ORDER BY id DESC", params
        ).fetchall()
        conn.close()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Movimentações de Caixa"

    headers = [
        "ID",
        "DATA / HORA",
        "CARTÃO Nº",
        "OPERADORA",
        "TIPO",
        "VALOR ESTADIA (R$)",
        "DESCONTADO CARTÃO (R$)",
        "COBRADO NO CAIXA (R$)",
        "SALDO ANTERIOR (R$)",
        "SALDO FINAL (R$)",
        "STATUS",
        "OBSERVAÇÃO",
    ]
    ws.append(headers)

    header_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font

    for r in rows:
        ws.append(
            [
                r["id"],
                r["criado_em"],
                r["cartao_numero"],
                r["usuario_nome"],
                r["tipo"],
                from_cents(r["valor_estadia_centavos"]),
                from_cents(r["valor_descontado_centavos"]),
                from_cents(r["valor_complemento_caixa_centavos"]),
                from_cents(r["saldo_anterior_centavos"]),
                from_cents(r["saldo_posterior_centavos"]),
                f"ESTORNADO ({r['estornado_por_nome']})" if r["estornado"] else "ATIVO",
                r["observacao"],
            ]
        )

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"EXTRATO_BAIXAS_CAIXA_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    print("Inicializando sistema...")
    init_db()
    print("Servidor iniciado! Acesse http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
