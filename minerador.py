import os
import tempfile
import git
import json
import subprocess
import requests
import shutil
from datetime import datetime
from dotenv import load_dotenv

# ==============================
# CONFIGURAÇÕES
# ==============================

load_dotenv()
TOKEN = os.getenv("GITHUB_TOKEN")
print("TOKEN LIDO:", TOKEN[:5], "...", TOKEN[-4:] if TOKEN else None)

DATASET = "dataset"
os.makedirs(DATASET, exist_ok=True)

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json"
}

QUERY = 'ruff language:Python'



# ==============================
# FUNÇÕES AUXILIARES
# ==============================

def log(msg):
    print(f"[INFO] {msg}")

def run_command(cmd, cwd=None):
    """Executa comando no shell e retorna stdout e stderr"""
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, shell=True, timeout=60)
        return result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return "", "⏱ Timeout ao executar comando"

def check_github_token(token):
    """Verifica se o token do GitHub é válido"""
    if not token:
        raise ValueError("❌ ERRO: GITHUB_TOKEN não encontrado no .env!")

    url = "https://api.github.com/user"
    headers = {"Authorization": f"token {token}"}
    r = requests.get(url, headers=headers)

    if r.status_code == 200:
        log(f"✅ Token válido! Autenticado como: {r.json().get('login')}")
    else:
        raise ValueError(f"❌ Token inválido ou sem permissão! Status {r.status_code}: {r.text}")

def search_repositories(query, max_pages=1):
    """Busca repositórios no GitHub que usam Ruff"""
    repos = []
    log("Buscando repositórios que usam Ruff...")
    for page in range(1, max_pages + 1):
        url = f"https://api.github.com/search/repositories?q={query}&per_page=50&page={page}" 
        r = requests.get(url, headers=HEADERS)
        if r.status_code != 200:
            raise ValueError(f"Erro {r.status_code} ao buscar repositórios: {r.text}")
        data = r.json()
        for item in data.get("items", []):
            repos.append(item["clone_url"])
    log(f"{len(repos)} repositórios encontrados.")
    return repos

def collect_ruff_violations(repo_path):
    """Executa Ruff e retorna lista de violações"""
    cmd = 'ruff check --output-format json .'
    stdout, stderr = run_command(cmd, cwd=repo_path)
    if not stdout.strip():
        return []
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        log(f"⚠ Erro ao decodificar saída Ruff: {stderr}")
        return []

def mine_repository(repo_url):
    """Percorre commits do repositório e coleta e salva violações"""
    repo_name = repo_url.split("/")[-1].replace(".git", "")
    owner = repo_url.split("/")[-2]
    temp_dir = tempfile.mkdtemp(prefix="repo_")
    violation_counter = 0

    log(f"--- Clonando repositório: {repo_url} ---")
    try:
        repo = git.Repo.clone_from(repo_url, temp_dir)
    except Exception as e:
        log(f"❌ Erro ao clonar {repo_url}: {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    try:
        branch = repo.active_branch.name
    except Exception:
        branch = "main"

    commits = list(repo.iter_commits(branch))
    log(f"{len(commits)} commits encontrados no branch {branch}")

    repo_base_dir = os.path.join(DATASET, repo_name)
    os.makedirs(repo_base_dir, exist_ok=True)

    for commit in commits:
        commit_hash = commit.hexsha
        short_hash = commit_hash[:7]

        try:
            repo.git.checkout(commit_hash)
        except Exception as e:
            log(f"❌ Erro ao fazer checkout no commit {short_hash}: {e}")
            continue

        log(f"→ Analisando commit {short_hash} ({datetime.fromtimestamp(commit.committed_date).strftime('%Y-%m-%d')})")
        violations = collect_ruff_violations(temp_dir)
        if not violations:
            continue

        commit_dir = os.path.join(repo_base_dir, short_hash)
        os.makedirs(commit_dir, exist_ok=True)

        for i, v in enumerate(violations):
            relative_file_path = v.get("filename")
            if not relative_file_path:
                continue

            file_path_full = os.path.join(temp_dir, relative_file_path)
            if not os.path.exists(file_path_full):
                log(f"⚠ Arquivo não encontrado no clone: {relative_file_path}")
                continue

            file_name = os.path.basename(relative_file_path)
            try:
                with open(file_path_full, "r", encoding="utf-8", errors="ignore") as f:
                    file_content = f.read()
            except Exception as e:
                log(f"❌ Erro ao ler conteúdo do arquivo {file_name}: {e}")
                continue

            python_file_destination = os.path.join(commit_dir, file_name)
            if not os.path.exists(python_file_destination):
                with open(python_file_destination, "w", encoding="utf-8", errors="ignore") as f:
                    f.write(file_content)

            violation_data = {
                "project_name": repo_name,
                "owner": owner,
                "branch": branch,
                "commit_hash": short_hash,
                "full_commit_hash": commit_hash,
                "commit_date": datetime.fromtimestamp(commit.committed_date).strftime("%Y-%m-%d"),
                "file_path_in_repo": relative_file_path,
                "local_file_name": file_name,
                "line": v["location"]["row"],
                "linter_code": v["code"],
                "message": v["message"],
                "repo_url": repo_url
            }

            violation_filename = f"violation_{v['code']}_{i+1}.json"
            json_destination = os.path.join(commit_dir, violation_filename)

            try:
                with open(json_destination, "w", encoding="utf-8") as f:
                    json.dump(violation_data, f, indent=4, ensure_ascii=False)
                violation_counter += 1
            except Exception as e:
                log(f"❌ Erro ao salvar JSON da violação: {e}")

    log(f"Processamento concluído. {violation_counter} violações salvas.")
    try:
        repo.close()
    except Exception:
        pass
    shutil.rmtree(temp_dir, ignore_errors=True)
    log(f"[OK] Repositório {repo_url} processado.")

# ==============================
# MAIN
# ==============================
if __name__ == "__main__":

    check_github_token(TOKEN)

    repos = search_repositories(QUERY, max_pages=5)  # max_pages=1 para teste rápido

    print(f"\n🚀 Iniciando mineração e salvamento estruturado em: {DATASET}/")
    for repo_url in repos[:10]: 
        mine_repository(repo_url)

    print("\n✅ Mineração estruturada concluída!")
