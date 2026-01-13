# Instalação do Módulo Voice Secretary no FusionPBX

## Pré-requisitos

1. **FusionPBX instalado** (versão 5.x ou superior)
2. **PostgreSQL** configurado e acessível
3. **Voice AI Service** rodando (Docker)
4. **Migrations executadas** no banco de dados

## Método 1: Script Automático (Recomendado)

```bash
# No servidor onde está o FusionPBX
cd /root/voice-ai-ivr

# Executar o instalador
sudo ./scripts/install-fusionpbx-app.sh

# Ou especificar o caminho do FusionPBX se diferente
sudo ./scripts/install-fusionpbx-app.sh /var/www/fusionpbx
```

O script irá:
1. Copiar os arquivos para `/var/www/fusionpbx/app/voice_secretary/`
2. Ajustar permissões
3. Executar os upgrades de schema, menu e permissões
4. Limpar o cache do PHP

## Método 2: Instalação Manual

### Passo 1: Copiar arquivos

```bash
# Copiar a pasta do aplicativo
sudo cp -r fusionpbx-app/voice_secretary /var/www/fusionpbx/app/

# Ajustar permissões
sudo chown -R www-data:www-data /var/www/fusionpbx/app/voice_secretary
sudo find /var/www/fusionpbx/app/voice_secretary -type f -exec chmod 644 {} \;
sudo find /var/www/fusionpbx/app/voice_secretary -type d -exec chmod 755 {} \;
```

### Passo 2: Executar Upgrades

```bash
cd /var/www/fusionpbx

# Criar tabelas no banco
sudo php core/upgrade/upgrade_schema.php

# Registrar menus
sudo php core/upgrade/upgrade_menu.php

# Atualizar permissões de grupo
sudo php core/upgrade/upgrade_permissions.php
```

### Passo 3: Limpar Cache

```bash
# Reiniciar PHP-FPM
sudo systemctl reload php8.1-fpm  # ou sua versão
```

## Método 3: Via Interface Web

1. Faça login no FusionPBX como **superadmin**
2. Vá para: **Advanced > Upgrade**
3. Clique em **App Defaults**
4. Clique em **Schema** 
5. Clique em **Menu**
6. Clique em **Permissions**

## Verificação da Instalação

### Verificar arquivos

```bash
ls -la /var/www/fusionpbx/app/voice_secretary/
```

Deve mostrar:
```
app_config.php
app_defaults.php
app_languages.php
app_menu.php
secretary.php
secretary_edit.php
providers.php
providers_edit.php
documents.php
documents_edit.php
conversations.php
conversation_detail.php
settings.php
transfer_rules.php
transfer_rules_edit.php
resources/
languages/
```

### Verificar tabelas no banco

```bash
sudo -u postgres psql fusionpbx -c "\dt v_voice_*"
```

Deve mostrar:
```
               List of relations
 Schema |          Name           | Type  |  Owner   
--------+-------------------------+-------+----------
 public | v_voice_ai_providers    | table | fusionpbx
 public | v_voice_conversations   | table | fusionpbx
 public | v_voice_document_chunks | table | fusionpbx
 public | v_voice_documents       | table | fusionpbx
 public | v_voice_messages        | table | fusionpbx
 public | v_voice_secretaries     | table | fusionpbx
 public | v_voice_transfer_rules  | table | fusionpbx
```

### Verificar menu

1. Login no FusionPBX como admin
2. Procure no menu: **Apps > Voice Secretary**

## Estrutura do Aplicativo

```
/var/www/fusionpbx/app/voice_secretary/
├── app_config.php          # Configuração do app (tabelas, menus, permissões)
├── app_defaults.php        # Valores padrão
├── app_languages.php       # Traduções
├── app_menu.php            # Definição do menu
│
├── secretary.php           # Listagem de secretárias
├── secretary_edit.php      # Criar/editar secretária
│
├── providers.php           # Listagem de provedores IA
├── providers_edit.php      # Configurar provedor
├── providers_realtime_edit.php  # Provedores realtime
│
├── documents.php           # Listagem de documentos (RAG)
├── documents_edit.php      # Upload de documentos
│
├── conversations.php       # Histórico de conversas
├── conversation_detail.php # Detalhes/transcrição
│
├── transfer_rules.php      # Regras de transferência
├── transfer_rules_edit.php # Configurar regras
│
├── settings.php            # Configurações gerais
│
├── resources/
│   ├── classes/
│   │   ├── voice_secretary.php     # Classe principal
│   │   ├── voice_ai_provider.php   # Gerenciamento de provedores
│   │   └── domain_validator.php    # Validação multi-tenant
│   ├── dashboard/                  # Widgets do dashboard
│   └── functions/                  # Funções auxiliares
│
└── languages/
    ├── en-us/                      # Inglês
    └── pt-br/                      # Português
```

## Configuração Inicial

### 1. Configurar Provedores de IA

Acesse: **Apps > Voice Secretary > AI Providers**

Configure pelo menos um provedor de cada tipo:

| Tipo | Provedores Suportados |
|------|----------------------|
| STT | OpenAI Whisper, Azure Speech, Google Cloud, Deepgram |
| TTS | OpenAI, ElevenLabs, Azure Neural, Google Cloud |
| LLM | OpenAI GPT, Anthropic Claude, Google Gemini, Groq |
| Embeddings | OpenAI, Azure, Cohere, Local |

### 2. Criar Secretária Virtual

Acesse: **Apps > Voice Secretary > Secretaries > Add**

Preencha:
- **Nome**: Nome da secretária
- **Empresa**: Nome da empresa
- **Prompt de Personalidade**: Instruções para a IA
- **Saudação**: Mensagem inicial
- **Despedida**: Mensagem de encerramento
- **Extensão**: Ramal para atendimento (ex: 8000)
- **Modo**: Turn-based (v1) ou Realtime (v2)

### 3. Configurar Dialplan

O dialplan deve ser configurado no FreeSWITCH para direcionar chamadas à secretária. Veja `docs/FREESWITCH_INTEGRATION.md`.

## Troubleshooting

### Menu não aparece

```bash
# Forçar atualização do menu
cd /var/www/fusionpbx
sudo php core/upgrade/upgrade_menu.php
```

Ou via interface:
1. Advanced > Upgrade > Menu

### Permissões negadas

```bash
# Verificar permissões
ls -la /var/www/fusionpbx/app/voice_secretary/

# Corrigir
sudo chown -R www-data:www-data /var/www/fusionpbx/app/voice_secretary
```

### Tabelas não criadas

```bash
# Verificar se o app_config.php tem erros de sintaxe
php -l /var/www/fusionpbx/app/voice_secretary/app_config.php

# Forçar recriação do schema
cd /var/www/fusionpbx
sudo php core/upgrade/upgrade_schema.php
```

### Erro 500 ao acessar páginas

```bash
# Verificar logs do PHP
tail -f /var/log/php8.1-fpm.log

# Verificar logs do Apache/Nginx
tail -f /var/log/nginx/error.log
```

## URLs do Módulo

Após instalação, o módulo estará acessível em:

- **Secretárias**: `https://seu-fusionpbx/app/voice_secretary/secretary.php`
- **Provedores**: `https://seu-fusionpbx/app/voice_secretary/providers.php`
- **Documentos**: `https://seu-fusionpbx/app/voice_secretary/documents.php`
- **Conversas**: `https://seu-fusionpbx/app/voice_secretary/conversations.php`
- **Configurações**: `https://seu-fusionpbx/app/voice_secretary/settings.php`
