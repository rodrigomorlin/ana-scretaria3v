# Ana v3 · Secretária Virtual de Anestesiologia

Sistema completo com PostgreSQL, email, Google Calendar, relatórios e PWA.

---

## 🚀 Deploy no Railway

### 1. Suba no GitHub
Substitua os arquivos do repositório pelos desta pasta.

### 2. Adicione PostgreSQL no Railway
1. No projeto Railway → **New** → **Database** → **PostgreSQL**
2. O Railway cria automaticamente a variável `DATABASE_URL`
3. A Ana detecta e usa PostgreSQL automaticamente

### 3. Variáveis de ambiente no Railway
Vá em **Settings → Variables** e adicione conforme necessário:

```
SECRET_KEY=qualquer_string_aleatoria_longa

# Email (opcional — notificações automáticas)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=seuemail@gmail.com
SMTP_PASS=sua_senha_de_app_gmail
SMTP_FROM=ana@seugrupo.com

# Google Calendar (opcional)
GCAL_CREDENTIALS={"type":"service_account",...}  # JSON da conta de serviço
GCAL_CALENDAR_ID=primary  # ou ID do calendário compartilhado
```

---

## 📧 Configurar Email (Gmail)

1. Ative **verificação em duas etapas** na conta Google
2. Gere uma **senha de app**: myaccount.google.com/apppasswords
3. Use essa senha em `SMTP_PASS`
4. Cadastre o email de cada médico no sistema (Config → Médicos)

---

## 📅 Configurar Google Calendar

1. Acesse **console.cloud.google.com**
2. Crie um projeto → ative a **Google Calendar API**
3. Crie uma **conta de serviço** → gere chave JSON
4. Cole o JSON completo na variável `GCAL_CREDENTIALS`
5. Compartilhe o calendário desejado com o email da conta de serviço
6. Coloque o ID do calendário em `GCAL_CALENDAR_ID`

---

## 💻 Rodar localmente

```bash
pip install -r requirements.txt
uvicorn main:app --reload
# http://localhost:8000
```

---

## ✨ Funcionalidades v3

- 🔐 Login por PIN com sessões seguras
- 🗄️ PostgreSQL (produção) ou SQLite (local) automático
- 📧 Email automático para o médico ao ser escalado
- 📅 Google Calendar — cria e remove eventos automaticamente
- 📊 Relatórios com 4 gráficos (por médico, setor, mês, dia da semana)
- 🧠 Memória adaptativa com ranking por uso
- 📄 Leitura de PDFs de pedidos médicos
- 🖨️ Exportação de agenda em PDF
- 📱 PWA — instala no celular como app
- 🔌 Painel de integrações com status em tempo real
- 📋 Logs de auditoria (admin)
- 👥 Gestão de usuários com roles (admin/médico)

---

## API

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | /api/login | Autenticação |
| GET | /api/me | Usuário atual |
| GET | /api/eventos | Lista agendamentos |
| POST | /api/eventos | Cria (valida conflito + email + Calendar) |
| DELETE | /api/eventos/{id} | Remove (+ remove do Calendar) |
| GET | /api/medicos | Lista médicos |
| POST/PUT/DELETE | /api/medicos/{id} | CRUD médicos |
| GET/POST/DELETE | /api/setores | CRUD setores |
| GET/POST/DELETE | /api/memorias | CRUD memórias |
| GET | /api/contexto-ia | Contexto otimizado para a IA |
| GET | /api/relatorios/resumo | Dados para gráficos |
| GET | /api/logs | Logs (admin) |
| GET | /api/health | Status das integrações |
