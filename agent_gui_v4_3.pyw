import json
import smtplib
import os
import socket
import threading
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import tkinter as tk
from tkinter import filedialog

import requests
from PIL import Image, ImageDraw
import pystray
import ctypes
import sys
from dotenv import load_dotenv

# ─── Paths ────────────────────────────────────────────────────────────────────

# Funciona tanto rodando como .pyw quanto empacotado como .exe
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

GROQ_API_KEY  = _obrigatorio("GROQ_API_KEY")
GROQ_MODEL    = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"

SMTP_HOST     = _obrigatorio("SMTP_HOST")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = _obrigatorio("SMTP_USER")
SMTP_PASSWORD = _obrigatorio("SMTP_PASSWORD")
EMAIL_TECNICO = _obrigatorio("EMAIL_TECNICO")

TICKETS_FILE  = os.path.join(BASE_DIR, "tickets.json")
USUARIO_FILE  = os.path.join(BASE_DIR, "usuario_maquina.json")

# ─── Cores ────────────────────────────────────────────────────────────────────

COR_FUNDO        = "#1a1a2e"
COR_PAINEL       = "#16213e"
COR_HEADER       = "#0f3460"
COR_BALAO_AGENTE = "#0f3460"
COR_BALAO_USER   = "#e94560"
COR_TEXTO        = "#eaeaea"
COR_TEXTO_SUAVE  = "#a0a8c0"
COR_INPUT        = "#1a1a2e"
COR_INPUT_BORDA  = "#0f3460"
COR_BOTAO        = "#e94560"
COR_BOTAO_HOVER  = "#c73652"
COR_DIGITANDO    = "#a0a8c0"

# ─── Utilitários ──────────────────────────────────────────────────────────────

def obter_ip_local() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "IP nao encontrado"

def carregar_tickets() -> list:
    if os.path.exists(TICKETS_FILE):
        with open(TICKETS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def salvar_tickets(tickets: list):
    with open(TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(tickets, f, ensure_ascii=False, indent=2)

def proximo_sequencial(tickets: list, ip: str) -> int:
    return len([t for t in tickets if t.get("ip_maquina") == ip]) + 1

def carregar_usuario_maquina() -> dict:
    if os.path.exists(USUARIO_FILE):
        with open(USUARIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def salvar_usuario_maquina(dados: dict):
    with open(USUARIO_FILE, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)

# ─── Anexo pendente (compartilhado) ───────────────────────────────────────────

_anexo_pendente = []

# ─── Ferramentas ──────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "registrar_e_notificar",
            "description": (
                "Registra o ticket de suporte com os dados coletados do usuario "
                "e envia um e-mail automatico para o tecnico responsavel. "
                "Chame SOMENTE apos coletar: nome, e-mail, setor e descricao do erro."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nome":             {"type": "string"},
                    "email_usuario":    {"type": "string"},
                    "setor":            {"type": "string"},
                    "descricao_erro":   {"type": "string"},
                    "solucao_sugerida": {"type": "string"}
                },
                "required": ["nome", "email_usuario", "setor", "descricao_erro", "solucao_sugerida"]
            }
        }
    }
]

def registrar_e_notificar(nome, email_usuario, setor, descricao_erro, solucao_sugerida):
    tickets    = carregar_tickets()
    ip_maquina = obter_ip_local()
    sequencial = proximo_sequencial(tickets, ip_maquina)
    ticket_id  = f"TKT-{ip_maquina}-{sequencial:04d}"
    criado_em  = datetime.now().strftime("%d/%m/%Y %H:%M")

    ticket = {
        "id": ticket_id, "ip_maquina": ip_maquina,
        "nome": nome, "email_usuario": email_usuario,
        "setor": setor, "descricao_erro": descricao_erro,
        "solucao_sugerida": solucao_sugerida,
        "criado_em": criado_em, "status": "aberto"
    }
    tickets.append(ticket)
    salvar_tickets(tickets)
    salvar_usuario_maquina({"nome": nome, "email": email_usuario,
                            "setor": setor, "ip": ip_maquina})

    corpo_email = f"""Novo chamado de suporte aberto.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  TICKET: {ticket_id}
  Data:   {criado_em}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

USUARIO
  Nome:    {nome}
  E-mail:  {email_usuario}
  Setor:   {setor}
  IP:      {ip_maquina}

PROBLEMA RELATADO
  {descricao_erro}

SOLUCAO SUGERIDA PELO AGENTE
  {solucao_sugerida}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━""".strip()

    anexo = _anexo_pendente[0] if _anexo_pendente else None
    resultado_email = enviar_email(
        destinatario=EMAIL_TECNICO,
        assunto=f"[{ticket_id}] Novo chamado - {setor} - {nome}",
        corpo=corpo_email,
        anexo_path=anexo
    )
    _anexo_pendente.clear()

    return {
        "ticket_id":     ticket_id,
        "ip_maquina":    ip_maquina,
        "criado_em":     criado_em,
        "email_enviado": resultado_email["sucesso"],
        "erro_email":    resultado_email.get("erro")
    }

def enviar_email(destinatario, assunto, corpo, anexo_path=None):
    try:
        msg = MIMEMultipart()
        msg["From"]    = SMTP_USER
        msg["To"]      = destinatario
        msg["Subject"] = assunto
        msg.attach(MIMEText(corpo, "plain", "utf-8"))

        if anexo_path and os.path.exists(anexo_path):
            with open(anexo_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition",
                            f"attachment; filename={os.path.basename(anexo_path)}")
            msg.attach(part)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_USER, destinatario, msg.as_string())
        return {"sucesso": True}
    except Exception as e:
        return {"sucesso": False, "erro": str(e)}

def executar_ferramenta(nome, argumentos):
    if nome == "registrar_e_notificar":
        resultado = registrar_e_notificar(**argumentos)
    else:
        resultado = {"erro": f"Ferramenta '{nome}' nao encontrada"}
    return json.dumps(resultado, ensure_ascii=False)

def chamar_groq(mensagens):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}",
               "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL, "messages": mensagens,
        "tools": TOOLS, "tool_choice": "auto", "temperature": 0.3
    }
    response = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()

# ─── System Tray ──────────────────────────────────────────────────────────────

def criar_icone_tray():
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill="#0f3460")
    draw.rectangle([18, 16, 46, 36], fill="#eaeaea", outline="#a0a8c0", width=1)
    draw.rectangle([24, 40, 40, 44], fill="#e94560")
    draw.rectangle([16, 44, 48, 47], fill="#a0a8c0")
    return img

# ─── System Prompt ────────────────────────────────────────────────────────────

def montar_system_prompt():
    ip      = obter_ip_local()
    usuario = carregar_usuario_maquina()

    if usuario and usuario.get("ip") == ip:
        contexto = (
            f"CONTEXTO DA MAQUINA: O ultimo usuario registrado neste computador (IP: {ip}) "
            f"foi '{usuario['nome']}' do setor '{usuario['setor']}' com e-mail '{usuario['email']}'. "
            f"Pergunte logo no inicio se ainda e o mesmo usuario. "
            f"Se sim, use esses dados automaticamente sem pedir de novo. "
            f"Se nao, colete os novos dados normalmente."
        )
    else:
        contexto = (
            f"CONTEXTO DA MAQUINA: Primeiro uso neste computador (IP: {ip}). "
            f"Colete todos os dados normalmente."
        )

    return f"""Voce e um agente de suporte tecnico de TI da Plasticos Maua. Seu objetivo e:

1. Cumprimentar o usuario cordialmente
2. {contexto}
3. Coletar as seguintes informacoes UMA POR VEZ (se nao disponiveis pelo contexto):
   - Nome completo
   - E-mail
   - Setor/departamento
   - Descricao detalhada do problema
4. Sugerir uma solucao com base no problema descrito
5. Chamar a ferramenta registrar_e_notificar com todos os dados
6. Confirmar o numero do ticket gerado ao usuario

Regras:
- SEMPRE escreva em portugues brasileiro, sem excecoes
- NUNCA escreva comentarios ou pensamentos em ingles
- Responda APENAS o que sera exibido ao usuario
- Faca apenas UMA pergunta por vez
- So chame a ferramenta depois de ter TODOS os 4 dados
- Apos registrar, informe o numero do ticket ao usuario"""

# ─── Interface Gráfica ────────────────────────────────────────────────────────

class ChatApp:
    def __init__(self, root):
        self.root      = root
        self.root.title("Suporte Técnico — TI")
        self.root.geometry("620x700")
        self.root.minsize(480, 500)
        self.root.configure(bg=COR_FUNDO)
        self.root.resizable(True, True)
        self.root.withdraw()  # inicia oculto na bandeja
        self.root.protocol("WM_DELETE_WINDOW", self._minimizar_para_tray)

        self.historico  = [{"role": "system", "content": montar_system_prompt()}]
        self.digitando  = False
        self.tray_icon  = None

        self._build_ui()
        self._iniciar_tray()
        self._iniciar_conversa()
        self._escutar_segunda_instancia()

    def _escutar_segunda_instancia(self):
        """Fica verificando se outra instância tentou abrir — se sim, mostra a janela."""
        WM_USER = 0x0400 + 1

        def verificar():
            # Registra um hook simples via after para checar flag
            pass

        # Usa after para checar um arquivo-flag periodicamente
        flag_file = os.path.join(BASE_DIR, ".show_window")

        def checar_flag():
            if os.path.exists(flag_file):
                try:
                    os.remove(flag_file)
                except Exception:
                    pass
                self._mostrar_janela()
            self.root.after(500, checar_flag)

        self.root.after(500, checar_flag)

    def _iniciar_countdown(self, segundos=5):
        """Exibe countdown e encerra o programa após ticket registrado."""
        print("encerrando em 5 seg, até mais")
        if segundos > 0:
            self.root.after(1000, lambda: self._iniciar_countdown(segundos - 1))
        else:
            self._sair_app()

    # ── Bandeja ───────────────────────────────────────────────────────────────

    def _iniciar_tray(self):
        img  = criar_icone_tray()
        menu = pystray.Menu(
            pystray.MenuItem("Abrir Suporte", self._mostrar_janela, default=True),
            pystray.MenuItem("Sair",          self._sair_app)
        )
        self.tray_icon = pystray.Icon("suporte_ti", img, "Suporte Técnico — TI", menu)
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

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=COR_HEADER, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="🖥", font=("Segoe UI Emoji", 22),
                 bg=COR_HEADER, fg=COR_TEXTO).pack(side="left", padx=16, pady=10)

        info = tk.Frame(header, bg=COR_HEADER)
        info.pack(side="left", pady=10)
        tk.Label(info, text="Suporte Técnico", font=("Segoe UI", 13, "bold"),
                 bg=COR_HEADER, fg=COR_TEXTO).pack(anchor="w")
        tk.Label(info, text="TI — Plásticos Mauá", font=("Segoe UI", 9),
                 bg=COR_HEADER, fg=COR_TEXTO_SUAVE).pack(anchor="w")

        tk.Label(header, text="● Online", font=("Segoe UI", 9),
                 bg=COR_HEADER, fg="#4caf50").pack(side="right", padx=16)

        # Área de mensagens
        frame_msgs = tk.Frame(self.root, bg=COR_FUNDO)
        frame_msgs.pack(fill="both", expand=True)

        self.canvas    = tk.Canvas(frame_msgs, bg=COR_FUNDO, highlightthickness=0, bd=0)
        scrollbar      = tk.Scrollbar(frame_msgs, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.msgs_inner    = tk.Frame(self.canvas, bg=COR_FUNDO)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.msgs_inner, anchor="nw")
        self.msgs_inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfig(
            self.canvas_window, width=e.width))
        self.canvas.bind_all("<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        # Label digitando
        self.label_digitando = tk.Label(self.root, text="",
                                        font=("Segoe UI", 9, "italic"),
                                        bg=COR_FUNDO, fg=COR_DIGITANDO)
        self.label_digitando.pack(anchor="w", padx=20)

        # Barra de anexo
        self.frame_anexo = tk.Frame(self.root, bg=COR_PAINEL)
        self.frame_anexo.pack(fill="x", padx=14, pady=(2, 0))
        self.label_anexo = tk.Label(self.frame_anexo, text="",
                                    font=("Segoe UI", 8), bg=COR_PAINEL, fg="#4caf50")
        self.label_anexo.pack(side="left")
        self.btn_rm_anexo = tk.Button(self.frame_anexo, text="✕",
                                      font=("Segoe UI", 8), bg=COR_PAINEL, fg="#e94560",
                                      relief="flat", cursor="hand2",
                                      command=self._remover_anexo)

        # Input
        input_frame = tk.Frame(self.root, bg=COR_PAINEL, pady=12)
        input_frame.pack(fill="x", side="bottom")

        inner = tk.Frame(input_frame, bg=COR_INPUT_BORDA)
        inner.pack(fill="x", padx=14)

        tk.Button(inner, text="📎", font=("Segoe UI", 13),
                  bg=COR_INPUT, fg=COR_TEXTO_SUAVE, relief="flat",
                  activebackground=COR_INPUT, cursor="hand2", bd=0, padx=8,
                  command=self._selecionar_anexo).pack(side="left", fill="y")

        self.entrada = tk.Text(inner, height=2, font=("Segoe UI", 11),
                               bg=COR_INPUT, fg=COR_TEXTO,
                               insertbackground=COR_TEXTO,
                               relief="flat", bd=8, wrap="word", padx=8, pady=6)
        self.entrada.pack(side="left", fill="both", expand=True)
        self.entrada.bind("<Return>",       self._enviar_enter)
        self.entrada.bind("<Shift-Return>", lambda e: None)

        self.btn_enviar = tk.Button(inner, text="➤", font=("Segoe UI", 14, "bold"),
                                    bg=COR_BOTAO, fg="white", relief="flat",
                                    activebackground=COR_BOTAO_HOVER,
                                    cursor="hand2", bd=0, padx=14,
                                    command=self._enviar_mensagem)
        self.btn_enviar.pack(side="right", fill="y")

        tk.Label(input_frame,
                 text="Enter para enviar  •  Shift+Enter nova linha  •  📎 anexar print",
                 font=("Segoe UI", 8), bg=COR_PAINEL,
                 fg=COR_TEXTO_SUAVE).pack(pady=(4, 0))

    # ── Anexo ─────────────────────────────────────────────────────────────────

    def _selecionar_anexo(self):
        path = filedialog.askopenfilename(
            title="Selecionar imagem ou arquivo",
            filetypes=[("Imagens", "*.png *.jpg *.jpeg *.bmp *.gif"),
                       ("Todos os arquivos", "*.*")]
        )
        if path:
            _anexo_pendente.clear()
            _anexo_pendente.append(path)
            self.label_anexo.config(text=f"📎 {os.path.basename(path)}")
            self.btn_rm_anexo.pack(side="left")

    def _remover_anexo(self):
        _anexo_pendente.clear()
        self.label_anexo.config(text="")
        self.btn_rm_anexo.pack_forget()

    # ── Balões ────────────────────────────────────────────────────────────────

    def _adicionar_balao(self, texto, lado="agente"):
        wrapper = tk.Frame(self.msgs_inner, bg=COR_FUNDO)
        wrapper.pack(fill="x", padx=14, pady=5)

        if lado == "agente":
            cor, anchor, side, prefixo = COR_BALAO_AGENTE, "w", "left", "🤖  "
        else:
            cor, anchor, side, prefixo = COR_BALAO_USER, "e", "right", ""

        balao = tk.Frame(wrapper, bg=cor)
        balao.pack(anchor=anchor, side=side)
        tk.Label(balao, text=prefixo + texto, font=("Segoe UI", 10),
                 bg=cor, fg=COR_TEXTO, wraplength=380,
                 justify="left", padx=12, pady=8).pack()
        self.root.after(50, lambda: self.canvas.yview_moveto(1.0))

    # ── Digitando ─────────────────────────────────────────────────────────────

    def _set_digitando(self, ativo):
        self.digitando = ativo
        if ativo:
            self.label_digitando.config(text="🤖  Agente está digitando...")
            self.btn_enviar.config(state="disabled", bg="#555")
            self.entrada.config(state="disabled")
        else:
            self.label_digitando.config(text="")
            self.btn_enviar.config(state="normal", bg=COR_BOTAO)
            self.entrada.config(state="normal")
            self.entrada.focus()

    # ── Envio ─────────────────────────────────────────────────────────────────

    def _enviar_enter(self, event):
        if not self.digitando:
            self._enviar_mensagem()
        return "break"

    def _enviar_mensagem(self):
        texto = self.entrada.get("1.0", "end").strip()
        if not texto or self.digitando:
            return
        self.entrada.delete("1.0", "end")

        display = f"{texto}\n📎 {os.path.basename(_anexo_pendente[0])}" \
                  if _anexo_pendente else texto
        self._adicionar_balao(display, lado="usuario")
        self.historico.append({"role": "user", "content": texto})
        self._set_digitando(True)
        threading.Thread(target=self._processar_resposta, daemon=True).start()

    # ── Groq ──────────────────────────────────────────────────────────────────

    def _iniciar_conversa(self):
        self._set_digitando(True)
        threading.Thread(target=self._primeira_mensagem, daemon=True).start()

    def _primeira_mensagem(self):
        try:
            r   = chamar_groq(self.historico)
            msg = r["choices"][0]["message"]["content"]
            self.historico.append({"role": "assistant", "content": msg})
            self.root.after(0, lambda: self._adicionar_balao(msg, "agente"))
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                msg = (
                    "⚠️ O sistema de atendimento atingiu o limite de uso do dia.\n\n"
                    "Por favor, entre em contato diretamente com o suporte:\n"
                    "📧 suporte@plasticosmaua.com.br"
                )
            else:
                msg = f"Erro ao conectar: {e}"
            self.root.after(0, lambda: self._adicionar_balao(msg, "agente"))
        except Exception as e:
            self.root.after(0, lambda: self._adicionar_balao(f"Erro: {e}", "agente"))
        finally:
            self.root.after(0, lambda: self._set_digitando(False))

    def _processar_resposta(self):
        try:
            while True:
                r        = chamar_groq(self.historico)
                choice   = r["choices"][0]
                mensagem = choice["message"]
                finish   = choice["finish_reason"]

                self.historico.append(mensagem)

                if mensagem.get("content"):
                    texto = mensagem["content"]
                    # Não exibe se for chamada de ferramenta vazada
                    if "<function=" not in texto:
                        self.root.after(0, lambda t=texto: self._adicionar_balao(t, "agente"))

                if finish != "tool_calls" or not mensagem.get("tool_calls"):
                    break

                for chamada in mensagem["tool_calls"]:
                    self.root.after(0, lambda: self._adicionar_balao(
                        "⚙️ Registrando ticket e enviando e-mail...", "agente"))

                    resultado      = executar_ferramenta(
                        chamada["function"]["name"],
                        json.loads(chamada["function"]["arguments"])
                    )
                    resultado_dict = json.loads(resultado)

                    if resultado_dict.get("email_enviado"):
                        self.root.after(0, lambda: self._adicionar_balao(
                            "✅ E-mail enviado ao técnico!", "agente"))
                        self.root.after(0, self._remover_anexo)
                        self.root.after(0, self._iniciar_countdown)
                    elif resultado_dict.get("erro_email"):
                        e = resultado_dict["erro_email"]
                        self.root.after(0, lambda e=e: self._adicionar_balao(
                            f"⚠️ Erro ao enviar e-mail: {e}", "agente"))

                    self.historico.append({
                        "role": "tool",
                        "tool_call_id": chamada["id"],
                        "content": resultado
                    })

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                msg = (
                    "⚠️ O sistema de atendimento atingiu o limite de uso do dia.\n\n"
                    "Por favor, entre em contato diretamente com o suporte:\n"
                    "📧 suporte@plasticosmaua.com.br"
                )
            else:
                msg = f"Erro: {e}"
            self.root.after(0, lambda: self._adicionar_balao(msg, "agente"))
        except Exception as e:
            self.root.after(0, lambda: self._adicionar_balao(f"Erro: {e}", "agente"))
        finally:
            self.root.after(0, lambda: self._set_digitando(False))


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Single instance via Mutex do Windows ──────────────────────────────────
    mutex      = ctypes.windll.kernel32.CreateMutexW(None, False, "SuporteTI_Mutex")
    ultimo_erro = ctypes.windll.kernel32.GetLastError()

    if ultimo_erro == 183:  # ERROR_ALREADY_EXISTS — já tem uma instância rodando
        # Cria arquivo-flag para sinalizar à instância existente que deve aparecer
        flag_file = os.path.join(BASE_DIR, ".show_window")
        with open(flag_file, "w") as f:
            f.write("show")
    else:
        root = tk.Tk()
        app  = ChatApp(root)
        root.mainloop()
