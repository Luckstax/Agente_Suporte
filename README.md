# Suporte Técnico — Agentes de IA (Plásticos Mauá)

Sistema de atendimento de suporte de TI com dois agentes baseados em LLM (Groq / Cerebras):

- **`agent_gui_v4_3.pyw`** — chat que roda na máquina do usuário (ícone na bandeja do
  Windows). Conversa com o usuário, coleta os dados do problema e, ao final, registra
  o ticket localmente e envia um e-mail automático para o técnico responsável.
- **`tecnico_3_2.pyw`** — painel do técnico. Sincroniza os tickets recebidos por e-mail
  (via POP3), exibe um painel com a lista de chamados e permite fechar tickets
  diretamente ou por chat com IA. Pode rodar em modo `principal` (quem sincroniza os
  e-mails) ou `secundario` (apenas lê os tickets de um caminho de rede compartilhado).

## ⚠️ Antes de rodar — configuração obrigatória

**Nenhuma credencial fica no código.** Todas as chaves de API e senhas são lidas de um
arquivo `.env`, que **não é enviado ao GitHub** (já está no `.gitignore`).

1. Copie o arquivo de exemplo:
   ```bash
   cp .env.example .env
   ```
2. Abra o `.env` e preencha os valores (veja a seção [Variáveis de ambiente](#variáveis-de-ambiente) abaixo).
3. Nunca faça commit do `.env` preenchido. Se algum valor real já foi commitado ou
   compartilhado, considere-o comprometido: troque a senha/chave imediatamente.

## Instalação

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# ou: source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
```

## Como rodar

```bash
python agent_gui_v4_3.pyw    # agente do usuário
python tecnico_3_2.pyw       # painel do técnico
```

Cada `.pyw` procura o arquivo `.env` na própria pasta onde ele está.

## Variáveis de ambiente

| Variável | Usado por | Obrigatória | Descrição |
|---|---|---|---|
| `GROQ_API_KEY` | ambos | ✅ | Chave da API da Groq ([console.groq.com/keys](https://console.groq.com/keys)) |
| `GROQ_MODEL` | ambos | não (tem padrão) | Modelo da Groq a ser usado |
| `CEREBRAS_API_KEY` | `tecnico_3_2.pyw` | não | Chave da Cerebras, usada só como fallback se a Groq falhar/atingir limite |
| `CEREBRAS_MODEL` | `tecnico_3_2.pyw` | não (tem padrão) | Modelo da Cerebras |
| `SMTP_HOST` / `SMTP_PORT` | `agent_gui_v4_3.pyw` | ✅ | Servidor de envio de e-mail (ticket → técnico) |
| `SMTP_USER` / `SMTP_PASSWORD` | `agent_gui_v4_3.pyw` | ✅ | Credenciais da conta que envia o e-mail do ticket |
| `EMAIL_TECNICO` | `agent_gui_v4_3.pyw` | ✅ | E-mail de destino que recebe os tickets abertos |
| `POP3_HOST` / `POP3_PORT` / `POP3_SSL` | `tecnico_3_2.pyw` | ✅ | Servidor de leitura de e-mail (recebe os tickets) |
| `POP3_USER` / `POP3_PASSWORD` | `tecnico_3_2.pyw` | ✅ | Credenciais da caixa de e-mail lida pelo painel do técnico |
| `MODO` | `tecnico_3_2.pyw` | não (padrão `principal`) | `principal` sincroniza e-mails; `secundario` só lê tickets da rede |
| `TICKETS_REDE` | `tecnico_3_2.pyw` | só se `MODO=secundario` | Caminho de rede compartilhado com o `tickets_tecnico.json` |

Se uma variável obrigatória não estiver definida, o programa exibe um erro claro
avisando para preencher o `.env` — ele não inicia com uma chave/senha vazia.

## Arquivos gerados em tempo de execução (não versionados)

Estes arquivos guardam dados reais de usuários (nome, e-mail, IP) e por isso **não
devem ir para o repositório** — já estão no `.gitignore`:

- `tickets.json` — tickets salvos pelo `agent_gui_v4_3.pyw`
- `tickets_tecnico.json` — tickets sincronizados pelo `tecnico_3_2.pyw`
- `usuario_maquina.json` — cache do último usuário identificado na máquina

Use `tickets_tecnico.example.json` como referência do formato, com dados fictícios.

## Empacotando como `.exe` (opcional)

Se for gerar um executável (ex.: com PyInstaller), gere-o **depois** de configurar o
`.env` corretamente e **não** commite o `.exe` no repositório — ele pode conter
resíduos de configuração e só deve ser distribuído fora do controle de versão.

## Segurança

- Trate toda credencial deste projeto (Groq, Cerebras, SMTP, POP3) como sensível.
- Se qualquer chave/senha já apareceu em texto puro em algum commit anterior,
  histórico do Git, print ou mensagem, **troque-a** — remover do arquivo não invalida
  uma credencial que já vazou.
- Os dados de tickets contêm informações pessoais de funcionários; evite compartilhar
  esse arquivo fora do ambiente interno da empresa.
