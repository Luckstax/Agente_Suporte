import json
import poplib
import email
import os
import sys
import threading
import re
import shutil
from datetime import datetime
from email.header import decode_header
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw
import pystray
import requests
from dotenv import load_dotenv

# ─── Paths ────────────────────────────────────────────────────────────────────

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ─── Configuração ─────────────────────────────────────────────────────────────
# Todas as credenciais vêm do arquivo .env (veja .env.example).
# Nenhum valor sensível deve ficar escrito diretamente no código.

def _obrigatorio(nome_var: str) -> str:
    """Lê uma variável de ambiente obrigatória; encerra com mensagem clara se faltar."""
    valor = os.getenv(nome_var)
    if not valor:
        raise RuntimeError(
            f"Variavel de ambiente '{nome_var}' nao encontrada. "
            f"Copie '.env.example' para '.env' e preencha os valores antes de rodar o programa."
        )
    return valor

# Fallback: tenta Groq primeiro, depois Cerebras.
# GROQ_API_KEY é obrigatória; CEREBRAS_API_KEY é opcional (fica em branco se
# não for usada — o código já ignora provedores sem chave configurada).
PROVEDORES = [
    {
        "nome":   "Groq",
        "url":    "https://api.groq.com/openai/v1/chat/completions",
        "chave":  _obrigatorio("GROQ_API_KEY"),
        "modelo": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    },
    {
        "nome":   "Cerebras",
        "url":    "https://api.cerebras.ai/v1/chat/completions",
        "chave":  os.getenv("CEREBRAS_API_KEY", ""),
        "modelo": os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
    },
]

POP3_HOST    = _obrigatorio("POP3_HOST")
POP3_PORT    = int(os.getenv("POP3_PORT", "110"))
POP3_USER    = _obrigatorio("POP3_USER")
POP3_PASSWORD= _obrigatorio("POP3_PASSWORD")
POP3_SSL     = os.getenv("POP3_SSL",     "false").lower() == "true"
POP3_TIMEOUT = 30

# MODO: "principal" (sincroniza e-mails) ou "secundario" (so le da rede)
MODO         = os.getenv("MODO", "principal").lower()
TICKETS_REDE = os.getenv("TICKETS_REDE", "")  # caminho de rede para o secundario

# O secundario lê da rede, o principal salva localmente
if MODO == "secundario" and TICKETS_REDE:
    TICKETS_FILE = TICKETS_REDE
else:
    TICKETS_FILE = os.path.join(BASE_DIR, "tickets_tecnico.json")

SYNC_INTERVAL = 60 * 60 * 1000  # 1 hora

_sync_lock = threading.Lock()

# ─── Cores ────────────────────────────────────────────────────────────────────

COR_FUNDO       = "#0d1117"
COR_PAINEL      = "#161b22"
COR_HEADER      = "#21262d"
COR_BORDA       = "#30363d"
COR_TEXTO       = "#e6edf3"
COR_TEXTO_SUAVE = "#8b949e"
COR_DESTAQUE    = "#58a6ff"
COR_VERDE       = "#238636"
COR_VERDE_HOVER = "#2ea043"
COR_VERMELHO    = "#b91c1c"
COR_BALAO_IA    = "#21262d"
COR_BALAO_USER  = "#1f6feb"
COR_INPUT       = "#0d1117"

# ─── JSON ─────────────────────────────────────────────────────────────────────

def carregar_tickets() -> list:
    if os.path.exists(TICKETS_FILE):
        try:
            with open(TICKETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
    return []

def salvar_tickets(tickets: list):
    tmp = TICKETS_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(tickets, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, TICKETS_FILE)
    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise e

def ordenar_tickets(tickets: list) -> list:
    """Ordena por data real (dd/mm/yyyy hh:mm), mais recente primeiro."""
    def parse_data(t):
        try:
            return datetime.strptime(t.get("criado_em", ""), "%d/%m/%Y %H:%M")
        except ValueError:
            return datetime.min
    return sorted(tickets, key=parse_data, reverse=True)

# ─── POP3 ─────────────────────────────────────────────────────────────────────

def decodificar_header(valor: str) -> str:
    partes = decode_header(valor)
    resultado = ""
    for parte, enc in partes:
        if isinstance(parte, bytes):
            resultado += parte.decode(enc or "utf-8", errors="ignore")
        else:
            resultado += str(parte)
    return resultado

def extrair_texto_email(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="ignore")
    return ""

def extrair_campo(corpo: str, campo: str) -> str:
    match = re.search(rf'{campo}:\s*(.+)', corpo)
    return match.group(1).strip() if match else ""

def parsear_ticket_do_email(assunto: str, corpo: str):
    try:
        match_id = re.search(r'\[([^\]]+)\]', assunto)
        if not match_id:
            return None
        ticket_id = match_id.group(1)
        if not ticket_id.startswith("TKT-"):
            return None

        nome          = extrair_campo(corpo, "Nome")
        email_usuario = extrair_campo(corpo, "E-mail")
        setor         = extrair_campo(corpo, "Setor")
        ip_maquina    = extrair_campo(corpo, "IP")

        match_prob = re.search(
            r'PROBLEMA RELATADO\s*\n\s*(.+?)(?:\n\n|\nSOLU)', corpo, re.DOTALL | re.IGNORECASE)
        descricao = match_prob.group(1).strip() if match_prob else ""

        match_sol = re.search(
            r'SOLUCAO SUGERIDA PELO AGENTE\s*\n\s*(.+?)(?:\n\n|[-=─━]{3,}|$)',
            corpo, re.DOTALL | re.IGNORECASE)
        solucao = match_sol.group(1).strip() if match_sol else ""

        match_data = re.search(r'Data:\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2})', corpo)
        criado_em  = match_data.group(1) if match_data else datetime.now().strftime("%d/%m/%Y %H:%M")

        if not nome and not ip_maquina:
            return None

        return {
            "id": ticket_id, "ip_maquina": ip_maquina,
            "nome": nome, "email_usuario": email_usuario,
            "setor": setor, "descricao_erro": descricao,
            "solucao_sugerida": solucao, "criado_em": criado_em,
            "status": "aberto", "resolucao": ""
        }
    except Exception:
        return None

def sincronizar_emails(callback_status=None) -> int:
    """Sincroniza e-mails (apenas tecnico principal) ou atualiza painel (secundario)."""
    if MODO == "secundario":
        if callback_status:
            callback_status("Modo secundario — lendo tickets da rede...")
        return 0

    if not _sync_lock.acquire(blocking=False):
        if callback_status:
            callback_status("Sincronizacao ja em andamento...")
        return 0

    novos = 0
    erros = 0
    caixa = None

    try:
        if callback_status:
            callback_status("Conectando ao servidor de e-mail...")

        caixa = poplib.POP3_SSL(POP3_HOST, POP3_PORT) if POP3_SSL else poplib.POP3(POP3_HOST, POP3_PORT)
        caixa.sock.settimeout(POP3_TIMEOUT)
        caixa.user(POP3_USER)
        caixa.pass_(POP3_PASSWORD)

        total = len(caixa.list()[1])
        if callback_status:
            callback_status(f"Verificando {total} e-mail(s)...")

        tickets        = carregar_tickets()
        ids_existentes = {t["id"] for t in tickets}

        for i in range(1, total + 1):
            try:
                linhas  = caixa.retr(i)[1]
                msg     = email.message_from_bytes(b"\n".join(linhas))
                assunto = decodificar_header(msg.get("Subject", ""))
                if "[TKT-" not in assunto:
                    continue
                ticket = parsear_ticket_do_email(assunto, extrair_texto_email(msg))
                if ticket is None:
                    erros += 1
                    continue
                if ticket["id"] not in ids_existentes:
                    tickets.append(ticket)
                    ids_existentes.add(ticket["id"])
                    novos += 1
            except Exception:
                erros += 1
                continue

        salvar_tickets(tickets)

        if novos > 0:
            status = f"Sincronizado! {novos} novo(s) ticket(s)."
        elif erros > 0:
            status = f"Sincronizado com {erros} erro(s) de leitura."
        else:
            status = "Sincronizado! Nenhum ticket novo."

        if callback_status:
            callback_status(status)

    except poplib.error_proto as e:
        if callback_status:
            callback_status(f"Erro POP3: {e}")
    except OSError as e:
        if callback_status:
            callback_status(f"Erro de conexao: {e}")
    except Exception as e:
        if callback_status:
            callback_status(f"Erro: {e}")
    finally:
        if caixa:
            try:
                caixa.quit()
            except Exception:
                pass
        _sync_lock.release()

    return novos

# ─── API com fallback ─────────────────────────────────────────────────────────

TOOLS_TECNICO = [
    {
        "type": "function",
        "function": {
            "name": "fechar_ticket",
            "description": "Marca um ticket como resolvido. So execute apos confirmacao explicita do tecnico.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticket_id": {"type": "string"},
                    "resolucao": {"type": "string"}
                },
                "required": ["ticket_id", "resolucao"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_tickets",
            "description": "Busca tickets por status, setor, IP ou nome.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["aberto", "fechado", "todos"]},
                    "filtro": {"type": "string"}
                },
                "required": ["status"]
            }
        }
    }
]

def chamar_api(mensagens: list, tool_choice: str = "auto") -> dict:
    """Tenta cada provedor em ordem. Pula para o próximo se receber 429."""
    ultimo_erro = None
    for provedor in PROVEDORES:
        if not provedor["chave"]:
            continue
        try:
            headers = {
                "Authorization": f"Bearer {provedor['chave']}",
                "Content-Type":  "application/json"
            }
            payload = {
                "model":       provedor["modelo"],
                "messages":    mensagens,
                "tools":       TOOLS_TECNICO,
                "tool_choice": tool_choice,
                "temperature": 0.3
            }
            response = requests.post(provedor["url"], headers=headers, json=payload, timeout=60)
            if response.status_code == 429:
                ultimo_erro = f"{provedor['nome']}: limite atingido"
                continue
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            ultimo_erro = str(e)
            continue
        except Exception as e:
            ultimo_erro = str(e)
            continue
    raise Exception(f"Todos os provedores falharam. Ultimo erro: {ultimo_erro}")

def fechar_ticket(ticket_id: str, resolucao: str) -> dict:
    tickets = carregar_tickets()
    for t in tickets:
        if t.get("id") == ticket_id:
            t["status"]     = "fechado"
            t["resolucao"]  = resolucao
            t["fechado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            salvar_tickets(tickets)
            return {"sucesso": True, "ticket_id": ticket_id}
    return {"sucesso": False, "erro": f"Ticket {ticket_id} nao encontrado"}

def buscar_tickets(status: str = "aberto", filtro: str = "") -> dict:
    tickets = carregar_tickets()
    if status != "todos":
        tickets = [t for t in tickets if t.get("status") == status]
    if filtro:
        f = filtro.lower()
        tickets = [t for t in tickets if
                   f in t.get("nome", "").lower() or
                   f in t.get("setor", "").lower() or
                   f in t.get("ip_maquina", "").lower()]
    return {"total": len(tickets), "tickets": tickets}

def executar_ferramenta(nome: str, argumentos: dict) -> str:
    if nome == "fechar_ticket":
        resultado = fechar_ticket(**argumentos)
    elif nome == "buscar_tickets":
        resultado = buscar_tickets(**argumentos)
    else:
        resultado = {"erro": f"Ferramenta '{nome}' nao encontrada"}
    return json.dumps(resultado, ensure_ascii=False)

def montar_system_prompt() -> str:
    tickets  = carregar_tickets()
    abertos  = [t for t in tickets if t.get("status") == "aberto"]
    fechados = [t for t in tickets if t.get("status") == "fechado"]

    lista = ""
    for t in ordenar_tickets(abertos):
        lista += f"  - {t['id']} | {t.get('nome','')} | {t.get('setor','')} | {t.get('criado_em','')} | Problema: {t.get('descricao_erro','')[:80]}\n"
    if not lista:
        lista = "  Nenhum ticket aberto.\n"

    return f"""Voce e um assistente de suporte tecnico de TI.

ESTADO ATUAL:
- Tickets abertos: {len(abertos)}
- Tickets fechados: {len(fechados)}

TICKETS ABERTOS (mais recentes primeiro):
{lista}
Voce pode: listar/filtrar tickets, fechar tickets, sugerir solucoes, analisar padroes.

Regras:
- SEMPRE responda em portugues brasileiro
- Seja objetivo e direto
- NUNCA feche tickets sem confirmacao explicita do tecnico
- NUNCA tome acoes autonomas
- Nunca escreva em ingles ou metacomentarios"""

# ─── Ícone da bandeja ─────────────────────────────────────────────────────────

def criar_icone_tray():
    img  = Image.new("RGB", (64, 64), color="#21262d")
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 10, 56, 40],  outline="#58a6ff", width=3)
    draw.rectangle([22, 40, 42, 48], fill="#58a6ff")
    draw.rectangle([16, 48, 48, 52], fill="#58a6ff")
    draw.ellipse([38, 4, 52, 18],    fill="#238636")
    return img

# ─── Interface ────────────────────────────────────────────────────────────────

class AgenteTecnico:
    def __init__(self, root):
        self.root      = root
        modo_label = "Principal" if MODO == "principal" else "Secundario"
        self.root.title(f"Agente Tecnico 3.1 - TI ({modo_label})")
        self.root.geometry("1200x750")
        self.root.minsize(950, 600)
        self.root.configure(bg=COR_FUNDO)
        self.root.withdraw()  # inicia oculto na bandeja

        self.historico = [{"role": "system", "content": montar_system_prompt()}]
        self.digitando = False
        self.tray_icon = None

        self.root.protocol("WM_DELETE_WINDOW", self._minimizar_para_tray)

        self._build_ui()
        self._iniciar_tray()
        self._sincronizar(inicial=True)
        self._agendar_sync()

    # ── Bandeja ───────────────────────────────────────────────────────────────

    def _iniciar_tray(self):
        img  = criar_icone_tray()
        menu = pystray.Menu(
            pystray.MenuItem("Abrir Painel", self._mostrar_janela, default=True),
            pystray.MenuItem("Sincronizar",  lambda i, it: self._sincronizar()),
            pystray.MenuItem("Sair",         self._sair_app)
        )
        self.tray_icon = pystray.Icon("tecnico_ti", img, "Agente Tecnico 3.0", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _minimizar_para_tray(self):
        self.root.withdraw()

    def _mostrar_janela(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)

    def _sair_app(self, icon=None, item=None):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.root.destroy)

    def _notificar_novos(self, novos: int):
        """Exibe popup de notificação quando chegar ticket novo."""
        if novos <= 0:
            return
        msg = f"{novos} novo(s) chamado(s) recebido(s)!"
        # Mostra notificação via tray
        if self.tray_icon:
            try:
                self.tray_icon.notify(msg, "Agente Tecnico — Novo Chamado")
            except Exception:
                pass
        # Também mostra popup se janela estiver aberta
        if self.root.winfo_viewable():
            self.root.after(0, lambda: messagebox.showinfo("Novo Chamado", msg))

    # ── Sync ──────────────────────────────────────────────────────────────────

    def _sincronizar(self, inicial=False):
        def rodar():
            novos = sincronizar_emails(callback_status=self._atualizar_status)
            self.root.after(0, self._atualizar_painel)
            # Secundario verifica se apareceu ticket novo no arquivo da rede
            if MODO == "secundario":
                tickets = carregar_tickets()
                abertos = len([t for t in tickets if t.get("status") == "aberto"])
                self.root.after(0, lambda: self._atualizar_status(f"Atualizado — {abertos} aberto(s)"))
            if novos > 0 and not inicial:
                self._notificar_novos(novos)
            # Exibe mensagem de sincronização no chat
            hora = datetime.now().strftime("%d/%m/%Y %H:%M")
            if novos > 0:
                msg_sync = f"Sincronizacao concluida as {hora} — {novos} novo(s) ticket(s) recebido(s)."
            else:
                msg_sync = f"Ultima sincronizacao: {hora} — Nenhum ticket novo."
            if not inicial:
                self.root.after(0, lambda m=msg_sync: self._adicionar_balao_sistema(m))
            if inicial:
                self.root.after(500, self._primeira_mensagem_ia)
        threading.Thread(target=rodar, daemon=True).start()

    def _agendar_sync(self):
        self.root.after(SYNC_INTERVAL, self._sync_automatico)

    def _sync_automatico(self):
        self._sincronizar()
        self._agendar_sync()

    def _atualizar_status(self, msg):
        self.root.after(0, lambda: self.lbl_status.config(text=msg))

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=COR_HEADER, height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="Painel Tecnico 3.0 - TI",
                 font=("Segoe UI", 13, "bold"),
                 bg=COR_HEADER, fg=COR_TEXTO).pack(side="left", padx=20, pady=14)

        self.lbl_status = tk.Label(header, text="",
                                   font=("Segoe UI", 9),
                                   bg=COR_HEADER, fg=COR_TEXTO_SUAVE)
        self.lbl_status.pack(side="left", padx=10)

        btn_txt = "Sincronizar" if MODO == "principal" else "Atualizar"
        tk.Button(header, text=btn_txt,
                  font=("Segoe UI", 9), bg=COR_VERDE, fg="white",
                  relief="flat", cursor="hand2", padx=12, pady=4,
                  activebackground=COR_VERDE_HOVER,
                  command=lambda: self._sincronizar()).pack(side="right", padx=8, pady=10)

        main = tk.Frame(self.root, bg=COR_FUNDO)
        main.pack(fill="both", expand=True)

        # ── Painel esquerdo ───────────────────────────────────────────────────
        painel = tk.Frame(main, bg=COR_PAINEL, width=520)
        painel.pack(side="left", fill="both", padx=(10, 5), pady=10)
        painel.pack_propagate(False)

        filtro_frame = tk.Frame(painel, bg=COR_PAINEL)
        filtro_frame.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(filtro_frame, text="Tickets", font=("Segoe UI", 11, "bold"),
                 bg=COR_PAINEL, fg=COR_TEXTO).pack(side="left")
        self.filtro_status = tk.StringVar(value="aberto")
        for txt, val in [("Abertos", "aberto"), ("Fechados", "fechado"), ("Todos", "todos")]:
            tk.Radiobutton(filtro_frame, text=txt, variable=self.filtro_status,
                           value=val, font=("Segoe UI", 9),
                           bg=COR_PAINEL, fg=COR_TEXTO_SUAVE,
                           selectcolor=COR_FUNDO, activebackground=COR_PAINEL,
                           command=self._atualizar_painel).pack(side="right", padx=4)

        lista_frame = tk.Frame(painel, bg=COR_PAINEL)
        lista_frame.pack(fill="both", expand=True, padx=10, pady=8)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=COR_FUNDO, foreground=COR_TEXTO,
                        fieldbackground=COR_FUNDO, rowheight=30, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background=COR_HEADER,
                        foreground=COR_DESTAQUE, font=("Segoe UI", 9, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", COR_DESTAQUE)],
                  foreground=[("selected", "#000")])

        colunas = ("status", "ticket", "nome", "setor", "data")
        self.tree = ttk.Treeview(lista_frame, columns=colunas, show="headings", selectmode="browse")
        self.tree.heading("status", text="")
        self.tree.heading("ticket", text="Ticket")
        self.tree.heading("nome",   text="Usuario")
        self.tree.heading("setor",  text="Setor")
        self.tree.heading("data",   text="Data")
        self.tree.column("status", width=24,  anchor="center", stretch=False)
        self.tree.column("ticket", width=165, anchor="w")
        self.tree.column("nome",   width=120, anchor="w")
        self.tree.column("setor",  width=90,  anchor="w")
        self.tree.column("data",   width=130, anchor="w")  # ← mais largo para data completa

        scroll_y = ttk.Scrollbar(lista_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        scroll_y.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._ao_selecionar_ticket)

        # Detalhe do ticket
        self.frame_detalhe = tk.Frame(painel, bg=COR_HEADER)
        self.frame_detalhe.pack(fill="x", padx=10, pady=(0, 4))
        self.txt_detalhe = tk.Text(self.frame_detalhe, height=10,  # ← maior
                                   font=("Segoe UI", 9), bg=COR_HEADER,
                                   fg=COR_TEXTO, relief="flat",
                                   wrap="word", padx=8, pady=6, state="disabled")
        self.txt_detalhe.pack(fill="x")

        # Botão fechar ticket direto no painel
        self.btn_fechar = tk.Button(painel, text="Fechar Ticket Selecionado",
                                    font=("Segoe UI", 9), bg=COR_VERMELHO, fg="white",
                                    relief="flat", cursor="hand2", pady=6,
                                    activebackground="#991b1b",
                                    command=self._fechar_ticket_painel)
        self.btn_fechar.pack(fill="x", padx=10, pady=(0, 10))

        # ── Chat direito ──────────────────────────────────────────────────────
        chat_frame = tk.Frame(main, bg=COR_FUNDO)
        chat_frame.pack(side="right", fill="both", expand=True, padx=(5, 10), pady=10)

        tk.Label(chat_frame, text="Assistente IA", font=("Segoe UI", 11, "bold"),
                 bg=COR_FUNDO, fg=COR_TEXTO).pack(anchor="w", padx=4)

        msgs_container = tk.Frame(chat_frame, bg=COR_FUNDO)
        msgs_container.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas    = tk.Canvas(msgs_container, bg=COR_FUNDO, highlightthickness=0, bd=0)
        scrollbar      = tk.Scrollbar(msgs_container, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.msgs_inner    = tk.Frame(self.canvas, bg=COR_FUNDO)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.msgs_inner, anchor="nw")
        self.msgs_inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self.canvas_window, width=e.width))
        self.canvas.bind_all("<MouseWheel>", lambda e: self.canvas.yview_scroll(
            int(-1 * (e.delta / 120)), "units"))

        self.lbl_digitando = tk.Label(chat_frame, text="",
                                      font=("Segoe UI", 9, "italic"),
                                      bg=COR_FUNDO, fg=COR_TEXTO_SUAVE)
        self.lbl_digitando.pack(anchor="w", padx=4)

        input_frame = tk.Frame(chat_frame, bg=COR_PAINEL, pady=10)
        input_frame.pack(fill="x", side="bottom")
        inner = tk.Frame(input_frame, bg=COR_BORDA)
        inner.pack(fill="x", padx=10)
        self.entrada = tk.Text(inner, height=2, font=("Segoe UI", 10),
                               bg=COR_INPUT, fg=COR_TEXTO,
                               insertbackground=COR_TEXTO,
                               relief="flat", bd=8, wrap="word", padx=8, pady=6)
        self.entrada.pack(side="left", fill="both", expand=True)
        self.entrada.bind("<Return>",       self._enviar_enter)
        self.entrada.bind("<Shift-Return>", lambda e: None)
        self.btn_enviar = tk.Button(inner, text="->", font=("Segoe UI", 13, "bold"),
                                    bg=COR_VERDE, fg="white", relief="flat",
                                    activebackground=COR_VERDE_HOVER,
                                    cursor="hand2", bd=0, padx=12,
                                    command=self._enviar_mensagem)
        self.btn_enviar.pack(side="right", fill="y")
        tk.Label(input_frame, text="Enter para enviar  -  Shift+Enter nova linha",
                 font=("Segoe UI", 8), bg=COR_PAINEL,
                 fg=COR_TEXTO_SUAVE).pack(pady=(4, 0))

    # ── Painel ────────────────────────────────────────────────────────────────

    def _atualizar_painel(self):
        sel_anterior = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        tickets = carregar_tickets()
        filtro  = self.filtro_status.get()
        if filtro != "todos":
            tickets = [t for t in tickets if t.get("status") == filtro]
        tickets = ordenar_tickets(tickets)
        for t in tickets:
            icone = "+" if t.get("status") == "aberto" else "v"
            self.tree.insert("", "end", iid=t["id"],
                values=(icone, t["id"], t.get("nome", ""),
                        t.get("setor", ""), t.get("criado_em", "")))
        # Restaura seleção anterior se ainda existir
        if sel_anterior and self.tree.exists(sel_anterior[0]):
            self.tree.selection_set(sel_anterior[0])
        abertos = len([t for t in carregar_tickets() if t.get("status") == "aberto"])
        self.lbl_status.config(text=f"{abertos} ticket(s) aberto(s)")

    def _ao_selecionar_ticket(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        ticket = next((t for t in carregar_tickets() if t["id"] == sel[0]), None)
        if not ticket:
            return
        self.txt_detalhe.config(state="normal")
        self.txt_detalhe.delete("1.0", "end")
        self.txt_detalhe.insert("end",
            f"ID: {ticket['id']}  |  IP: {ticket.get('ip_maquina','')}  |  Status: {ticket['status'].upper()}\n"
            f"Usuario: {ticket.get('nome','')} - {ticket.get('setor','')} - {ticket.get('email_usuario','')}\n"
            f"Data: {ticket.get('criado_em','')}\n\n"
            f"Problema:\n{ticket.get('descricao_erro','')}\n\n"
            f"Solucao sugerida:\n{ticket.get('solucao_sugerida','')}"
        )
        if ticket.get("resolucao"):
            self.txt_detalhe.insert("end",
                f"\n\nResolucao: {ticket['resolucao']}\nFechado em: {ticket.get('fechado_em','')}")
        self.txt_detalhe.config(state="disabled")

    def _fechar_ticket_painel(self):
        """Fecha o ticket selecionado diretamente pelo painel, sem usar o chat."""
        if MODO == "secundario":
            messagebox.showinfo("Aviso", "Somente o tecnico principal pode fechar tickets.")
            return
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Selecione um ticket para fechar.")
            return
        ticket_id = sel[0]
        ticket    = next((t for t in carregar_tickets() if t["id"] == ticket_id), None)
        if not ticket:
            return
        if ticket.get("status") == "fechado":
            messagebox.showinfo("Aviso", "Este ticket ja esta fechado.")
            return

        # Janela de resolução
        janela = tk.Toplevel(self.root)
        janela.title("Fechar Ticket")
        janela.geometry("420x220")
        janela.configure(bg=COR_PAINEL)
        janela.resizable(False, False)
        janela.grab_set()

        tk.Label(janela, text=f"Fechando: {ticket_id}",
                 font=("Segoe UI", 10, "bold"), bg=COR_PAINEL, fg=COR_TEXTO).pack(padx=16, pady=(14, 4))
        tk.Label(janela, text="Descreva a resolucao:",
                 font=("Segoe UI", 9), bg=COR_PAINEL, fg=COR_TEXTO_SUAVE).pack(padx=16, anchor="w")

        txt_res = tk.Text(janela, height=5, font=("Segoe UI", 9),
                          bg=COR_INPUT, fg=COR_TEXTO, insertbackground=COR_TEXTO,
                          relief="flat", padx=8, pady=6, wrap="word")
        txt_res.pack(fill="x", padx=16, pady=6)
        txt_res.focus()

        def confirmar():
            resolucao = txt_res.get("1.0", "end").strip()
            if not resolucao:
                messagebox.showwarning("Aviso", "Informe a resolucao.", parent=janela)
                return
            fechar_ticket(ticket_id, resolucao)
            janela.destroy()
            self._atualizar_painel()
            self._adicionar_balao(f"Ticket {ticket_id} fechado via painel.", "ia")

        tk.Button(janela, text="Confirmar Fechamento",
                  font=("Segoe UI", 9, "bold"), bg=COR_VERMELHO, fg="white",
                  relief="flat", cursor="hand2", pady=6,
                  command=confirmar).pack(fill="x", padx=16, pady=(0, 14))

    # ── Chat ──────────────────────────────────────────────────────────────────

    def _adicionar_balao_sistema(self, texto: str):
        """Exibe mensagem de sistema (cinza, centralizada) no chat."""
        wrapper = tk.Frame(self.msgs_inner, bg=COR_FUNDO)
        wrapper.pack(fill="x", padx=10, pady=2)
        tk.Label(wrapper, text=texto, font=("Segoe UI", 8, "italic"),
                 bg=COR_FUNDO, fg=COR_TEXTO_SUAVE,
                 anchor="center").pack(fill="x")
        self.root.after(50, lambda: self.canvas.yview_moveto(1.0))

    def _adicionar_balao(self, texto: str, lado: str = "ia"):
        wrapper = tk.Frame(self.msgs_inner, bg=COR_FUNDO)
        wrapper.pack(fill="x", padx=10, pady=4)
        if lado == "ia":
            cor, anchor, side, prefixo = COR_BALAO_IA, "w", "left", "[IA]  "
        else:
            cor, anchor, side, prefixo = COR_BALAO_USER, "e", "right", ""
        balao = tk.Frame(wrapper, bg=cor)
        balao.pack(anchor=anchor, side=side)
        tk.Label(balao, text=prefixo + texto, font=("Segoe UI", 10),
                 bg=cor, fg=COR_TEXTO, wraplength=360,
                 justify="left", padx=10, pady=7).pack()
        self.root.after(50, lambda: self.canvas.yview_moveto(1.0))

    def _set_digitando(self, ativo: bool):
        self.digitando = ativo
        if ativo:
            self.lbl_digitando.config(text="Agente processando...")
            self.btn_enviar.config(state="disabled", bg="#555")
            self.entrada.config(state="disabled")
        else:
            self.lbl_digitando.config(text="")
            self.btn_enviar.config(state="normal", bg=COR_VERDE)
            self.entrada.config(state="normal")
            self.entrada.focus()

    def _enviar_enter(self, event):
        if not self.digitando:
            self._enviar_mensagem()
        return "break"

    def _enviar_mensagem(self):
        texto = self.entrada.get("1.0", "end").strip()
        if not texto or self.digitando:
            return
        self.entrada.delete("1.0", "end")
        self._adicionar_balao(texto, lado="usuario")
        self.historico[0] = {"role": "system", "content": montar_system_prompt()}
        self.historico.append({"role": "user", "content": texto})
        self._set_digitando(True)
        threading.Thread(target=self._processar_resposta, daemon=True).start()

    def _primeira_mensagem_ia(self):
        self.historico[0] = {"role": "system", "content": montar_system_prompt()}
        self._set_digitando(True)
        threading.Thread(target=self._msg_inicial, daemon=True).start()

    def _msg_inicial(self):
        try:
            r   = chamar_api(self.historico, tool_choice="none")
            msg = r["choices"][0]["message"].get("content") or "Ola! Como posso ajudar?"
            self.historico.append({"role": "assistant", "content": msg})
            self.root.after(0, lambda: self._adicionar_balao(msg, "ia"))
        except Exception as e:
            self.root.after(0, lambda: self._adicionar_balao(f"Erro ao conectar: {e}", "ia"))
        finally:
            self.root.after(0, lambda: self._set_digitando(False))

    def _processar_resposta(self):
        try:
            while True:
                r        = chamar_api(self.historico)
                choice   = r["choices"][0]
                mensagem = choice["message"]
                finish   = choice["finish_reason"]

                msg_norm = {"role": "assistant", "content": mensagem.get("content") or ""}
                if mensagem.get("tool_calls"):
                    msg_norm["tool_calls"] = mensagem["tool_calls"]
                self.historico.append(msg_norm)

                if mensagem.get("content"):
                    texto = mensagem["content"]
                    if "<function=" not in texto:
                        self.root.after(0, lambda t=texto: self._adicionar_balao(t, "ia"))

                if finish != "tool_calls" or not mensagem.get("tool_calls"):
                    break

                for chamada in mensagem["tool_calls"]:
                    nome       = chamada["function"]["name"]
                    argumentos = json.loads(chamada["function"]["arguments"])
                    resultado  = executar_ferramenta(nome, argumentos)
                    res_dict   = json.loads(resultado)

                    if nome == "fechar_ticket" and res_dict.get("sucesso"):
                        self.root.after(0, self._atualizar_painel)

                    self.historico.append({
                        "role":         "tool",
                        "tool_call_id": chamada["id"],
                        "content":      resultado
                    })

        except Exception as e:
            self.root.after(0, lambda: self._adicionar_balao(f"Erro: {e}", "ia"))
        finally:
            self.root.after(0, lambda: self._set_digitando(False))


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app  = AgenteTecnico(root)
    root.mainloop()
