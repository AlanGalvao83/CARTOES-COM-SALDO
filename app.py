import os
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
from werkzeug.security import check_password_hash, generate_password_hash
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "estacionamento.db")
EXCEL_PATH = os.path.join(BASE_DIR, "RELATÓRIO DE DEFINIÇÃO DE CARTOES.xlsx")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "estacionamento-cartoes-saldo-secret-2026")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
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
    conn = get_db()
    cur = conn.cursor()

    # Tabela de usuários (operadoras de caixa e administradores)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            nome TEXT NOT NULL,
            senha_hash TEXT NOT NULL,
            perfil TEXT NOT NULL DEFAULT 'operadora', -- 'admin' ou 'operadora'
            ativo INTEGER NOT NULL DEFAULT 1,
            criado_em TEXT NOT NULL
        )
        """
    )

    # Tabela de cartões
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

    # Tabela de movimentações (histórico de descontos, ajustes e estornos)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS movimentacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cartao_id INTEGER NOT NULL,
            cartao_numero INTEGER NOT NULL,
            usuario_id INTEGER NOT NULL,
            usuario_nome TEXT NOT NULL,
            tipo TEXT NOT NULL, -- 'BAIXA_TOTAL', 'BAIXA_PARCIAL', 'ESTORNO', 'AJUSTE_ADMIN'
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

    # Criar usuários padrão se a tabela estiver vazia
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

    # Importar planilha automaticamente se a tabela de cartões estiver vazia
    cur.execute("SELECT COUNT(*) FROM cartoes")
    if cur.fetchone()[0] == 0 and os.path.exists(EXCEL_PATH):
        import_excel_to_db(conn, EXCEL_PATH)

    conn.close()


def import_excel_to_db(conn, filepath):
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)  # pula cabeçalho ('NÚMERO', 'GRUPO', 'SALDO', 'INÍCIO', 'FIM', 'NOME')

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
    return {
        "id": row["id"],
        "numero": row["numero"],
        "grupo": row["grupo"],
        "saldo_inicial": from_cents(row["saldo_inicial_centavos"]),
        "saldo": from_cents(row["saldo_centavos"]),
        "saldo_centavos": row["saldo_centavos"],
        "data_inicio": row["data_inicio"] or "",
        "data_fim": row["data_fim"] or "",
        "nome": row["nome"] or "",
        "observacao": row["observacao"] or "",
        "atualizado_em": row["atualizado_em"] or "",
        "precisa_atualizar_nome": (not row["nome"]) or (row["nome"].strip().upper() == "ATUALIZAÇÃO DE CADASTRO"),
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
        # Lista os usernames ativos para facilitar a seleção rápida no login do caixa
        conn = get_db()
        users = conn.execute(
            "SELECT username, nome, perfil FROM usuarios WHERE ativo = 1 ORDER BY perfil DESC, nome ASC"
        ).fetchall()
        conn.close()
        return jsonify(
            {
                "authenticated": False,
                "available_users": [dict(u) for u in users],
            }
        )
    return jsonify(
        {
            "authenticated": True,
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
    filtro_saldo = request.args.get("saldo", "todos")  # 'todos', 'com_saldo', 'zerados'
    limit = min(int(request.args.get("limit", 30)), 100)

    conn = get_db()
    params = []
    where_clauses = []

    if termo:
        if termo.isdigit():
            # Busca exata primeiro ou parcial por número
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

    # Se o termo for um número, prioriza correspondência exata na ordenação
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
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM cartoes WHERE numero = ? ORDER BY saldo_centavos DESC, id ASC",
        (numero,),
    ).fetchall()

    if not rows:
        conn.close()
        return jsonify({"error": f"Cartão nº {numero} não encontrado na base de dados."}), 404

    cartoes = [serialize_cartao(r) for r in rows]
    # Carrega o histórico do primeiro cartão retornado
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

        # Verifica se é baixa total da estadia ou baixa parcial (quando estadia > saldo do cartão)
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

        # Se a operadora informou o nome do cliente junto da baixa, já atualiza
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

        # Operadora comum só pode estornar lançamentos feitos no mesmo dia; Admin pode estornar qualquer um
        hoje = datetime.now().strftime("%Y-%m-%d")
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

        # Atualiza o cartão devolvendo o saldo
        conn.execute(
            "UPDATE cartoes SET saldo_centavos = ?, atualizado_em = ? WHERE id = ?",
            (novo_saldo, now, cartao["id"]),
        )

        # Marca a movimentação original como estornada
        conn.execute(
            """
            UPDATE movimentacoes
            SET estornado = 1, estornado_por_nome = ?, estornado_em = ?
            WHERE id = ?
            """,
            (session["nome"], now, mov_id),
        )

        # Registra a entrada do estorno no histórico
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
    conn = get_db()
    hoje = datetime.now().strftime("%Y-%m-%d")

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


@app.route("/api/usuarios/<int:user_id>", methods=["PUT"])
@admin_required
def api_atualizar_usuario(user_id):
    data = request.get_json(silent=True) or {}
    nome = (data.get("nome") or "").strip()
    nova_senha = (data.get("senha") or "").strip()
    perfil = data.get("perfil")
    ativo = data.get("ativo")

    conn = get_db()
    user = conn.execute("SELECT * FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "Usuário não encontrado."}), 404

    novo_nome = nome if nome else user["nome"]
    novo_perfil = perfil if perfil in ("operadora", "admin") else user["perfil"]
    novo_ativo = int(bool(ativo)) if ativo is not None else user["ativo"]
    nova_hash = generate_password_hash(nova_senha) if nova_senha else user["senha_hash"]

    conn.execute(
        """
        UPDATE usuarios
        SET nome = ?, senha_hash = ?, perfil = ?, ativo = ?
        WHERE id = ?
        """,
        (novo_nome, nova_hash, novo_perfil, novo_ativo, user_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"message": "Usuário atualizado com sucesso!"})


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
    print("Inicializando banco de dados e verificando planilha...")
    init_db()
    print("Servidor iniciado! Acesse http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

