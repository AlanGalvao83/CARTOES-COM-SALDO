import os
from datetime import datetime
import openpyxl
import requests
from werkzeug.security import generate_password_hash

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

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://snajtfygntpqvsqcpssv.supabase.co").rstrip("/")
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    or os.environ.get("SUPABASE_PUBLISHABLE_KEY")
    or ""
)


def sb_headers(prefer=None):
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h


def to_cents(val):
    if val is None or val == "":
        return 0
    if isinstance(val, str):
        val = val.replace("R$", "").replace(" ", "").replace(",", ".")
    return int(round(float(val) * 100))


def format_date_cell(val):
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    return s[:10] if len(s) >= 10 else s


def migrar():
    if not SUPABASE_KEY:
        print("ERRO: Variável SUPABASE_SECRET_KEY não configurada.")
        return False

    # 1. Verifica se a tabela 'usuarios' existe no Supabase
    r_check = requests.get(
        f"{SUPABASE_URL}/rest/v1/usuarios?select=id&limit=1",
        headers=sb_headers(),
        timeout=15,
    )
    if r_check.status_code == 404:
        print(
            "ATENÇÃO: As tabelas ainda não existem no Supabase!\n"
            "Abra o SQL Editor do Supabase, cole o conteúdo do arquivo 'supabase_schema.sql' e clique em Run.\n"
            "Em seguida, execute novamente: python migrar_para_supabase.py"
        )
        return False

    # 2. Cria usuários iniciais caso não existam
    if r_check.status_code == 200 and len(r_check.json()) == 0:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        default_users = [
            {
                "username": "admin",
                "nome": "Administrador",
                "senha_hash": generate_password_hash("admin123"),
                "perfil": "admin",
                "ativo": 1,
                "criado_em": now,
            },
            {
                "username": "caixa1",
                "nome": "Operadora Caixa 01",
                "senha_hash": generate_password_hash("1234"),
                "perfil": "operadora",
                "ativo": 1,
                "criado_em": now,
            },
            {
                "username": "caixa2",
                "nome": "Operadora Caixa 02",
                "senha_hash": generate_password_hash("1234"),
                "perfil": "operadora",
                "ativo": 1,
                "criado_em": now,
            },
        ]
        ru = requests.post(
            f"{SUPABASE_URL}/rest/v1/usuarios",
            headers=sb_headers("return=minimal"),
            json=default_users,
            timeout=15,
        )
        print("Usuários padrões criados no Supabase:", ru.status_code)

    # 3. Verifica se os cartões já foram importados
    r_cards = requests.get(
        f"{SUPABASE_URL}/rest/v1/cartoes?select=id&limit=1",
        headers=sb_headers("count=exact"),
        timeout=15,
    )
    content_range = r_cards.headers.get("Content-Range", "*/0")
    total_existing = int(content_range.split("/")[-1]) if "/" in content_range and content_range.split("/")[-1].isdigit() else 0
    if total_existing > 0:
        print(f"Supabase já possui {total_existing} cartões cadastrados.")
        return True

    print("Lendo planilha RELATÓRIO DE DEFINIÇÃO DE CARTOES.xlsx...")
    wb = openpyxl.load_workbook(EXCEL_PATH, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    next(rows, None)  # pula cabeçalho

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    batch = []
    total_enviados = 0

    for row in rows:
        if not row or row[0] is None:
            continue
        saldo_cents = to_cents(row[2])
        batch.append(
            {
                "numero": int(row[0]),
                "grupo": int(row[1]) if row[1] is not None else 1,
                "saldo_inicial_centavos": saldo_cents,
                "saldo_centavos": saldo_cents,
                "data_inicio": format_date_cell(row[3]),
                "data_fim": format_date_cell(row[4]),
                "nome": str(row[5]).strip() if row[5] is not None else "",
                "observacao": "",
                "atualizado_em": now,
            }
        )
        if len(batch) >= 1000:
            res = requests.post(
                f"{SUPABASE_URL}/rest/v1/cartoes",
                headers=sb_headers("return=minimal"),
                json=batch,
                timeout=30,
            )
            if res.status_code not in (200, 201):
                print("Erro ao enviar lote:", res.status_code, res.text)
                wb.close()
                return False
            total_enviados += len(batch)
            print(f"Enviados {total_enviados} cartões para o Supabase...")
            batch = []

    if batch:
        res = requests.post(
            f"{SUPABASE_URL}/rest/v1/cartoes",
            headers=sb_headers("return=minimal"),
            json=batch,
            timeout=30,
        )
        if res.status_code not in (200, 201):
            print("Erro ao enviar lote final:", res.status_code, res.text)
            wb.close()
            return False
        total_enviados += len(batch)

    wb.close()
    print(f"SUCESSO! Todos os {total_enviados} cartões foram importados para o Supabase!")
    return True


if __name__ == "__main__":
    migrar()
