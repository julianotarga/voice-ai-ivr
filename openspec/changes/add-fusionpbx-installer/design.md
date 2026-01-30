# Design: Instalador Automatizado para FusionPBX

## Context

O Voice AI IVR é um módulo que adiciona secretária virtual com IA ao FusionPBX. A instalação requer:
- Tabelas PostgreSQL seguindo padrão FusionPBX (`v_` prefix, `domain_uuid` obrigatório)
- Aplicação PHP no padrão de apps do FusionPBX
- Serviço Python rodando como daemon
- Integração com FreeSWITCH via ESL e dialplan

**Stakeholders**: Administradores de sistemas, integradores, suporte técnico

**Constraints**:
- Não pode alterar arquivos core do FusionPBX
- Deve funcionar com diferentes versões do FusionPBX (5.x)
- Deve ser idempotente (pode rodar múltiplas vezes)
- Deve preservar configurações existentes

## Goals / Non-Goals

### Goals
- Automatizar 100% do processo de instalação
- Suportar instalação, atualização e desinstalação
- Detectar e validar ambiente existente
- Fornecer rollback em caso de falha
- Gerar logs detalhados para troubleshooting

### Non-Goals
- Instalação do FusionPBX do zero
- Gerenciamento de licenças
- Configuração de SSL/TLS
- Integração com sistemas de monitoramento externos

## Decisions

### 1. Estrutura do Instalador

**Decisão**: Script Bash modular com funções separadas por responsabilidade

**Alternativas consideradas**:
- Ansible playbook: Mais complexo, requer Ansible instalado
- Python installer: Dependência circular (precisa Python para instalar Python)
- Docker: Não se integra bem com FusionPBX existente

**Estrutura de arquivos**:
```
deploy/
├── install-fusionpbx.sh          # Script principal
├── lib/
│   ├── common.sh                 # Funções comuns (log, cores, verificações)
│   ├── detect.sh                 # Detecção de ambiente
│   ├── database.sh               # Operações de banco
│   ├── fusionpbx-app.sh          # Instalação do app PHP
│   ├── voice-service.sh          # Instalação do serviço Python
│   ├── dialplan.sh               # Configuração de dialplan
│   └── rollback.sh               # Funções de rollback
├── templates/
│   ├── voice-ai.env.template     # Template de configuração
│   ├── dialplan-voice-ai.sql     # SQL para inserir dialplan no banco
│   └── systemd/                  # Units do systemd
├── migrations/
│   └── consolidated.sql          # Todas migrations em ordem
└── tools/
    ├── voice-ai-status.sh        # Verificação de saúde
    ├── voice-ai-logs.sh          # Visualização de logs
    └── voice-ai-uninstall.sh     # Desinstalação
```

### 2. Detecção de Ambiente

**Decisão**: Auto-detecção com fallback para input manual

O instalador detectará automaticamente:
- Caminho do FusionPBX (`/var/www/fusionpbx` ou customizado)
- Credenciais PostgreSQL via `/etc/fusionpbx/config.php`
- Versão do FusionPBX
- Configuração do FreeSWITCH
- Senha do ESL

```bash
# Exemplo de detecção
FUSIONPBX_PATH=$(find /var/www -name "config.php" -path "*/fusionpbx/*" 2>/dev/null | head -1 | xargs dirname)
DB_HOST=$(grep "db_host" "$FUSIONPBX_PATH/config.php" | cut -d"'" -f4)
DB_NAME=$(grep "db_name" "$FUSIONPBX_PATH/config.php" | cut -d"'" -f4)
DB_USER=$(grep "db_username" "$FUSIONPBX_PATH/config.php" | cut -d"'" -f4)
DB_PASS=$(grep "db_password" "$FUSIONPBX_PATH/config.php" | cut -d"'" -f4)
```

### 3. Execução de Migrations

**Decisão**: Arquivo SQL consolidado com verificações de idempotência

Cada migration terá:
- `CREATE TABLE IF NOT EXISTS`
- `DO $$ BEGIN ... EXCEPTION WHEN ... END $$` para ADD COLUMN
- Verificação de constraints existentes antes de criar

```sql
-- Exemplo de migration idempotente
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'v_voice_secretaries' AND column_name = 'new_column') 
    THEN
        ALTER TABLE v_voice_secretaries ADD COLUMN new_column TEXT;
    END IF;
END $$;
```

### 4. Instalação do App FusionPBX

**Decisão**: Cópia direta + execução de upgrade.php

**Estrutura de um app FusionPBX** (baseado em `fusionpbx-app/voice_secretary/`):

```
voice_secretary/
├── app_config.php       # Configuração principal (menu, permissões, schema)
├── app_defaults.php     # Valores padrão e inicialização
├── app_languages.php    # Traduções i18n
├── app_menu.php         # Definição de menu (alternativo)
├── secretary.php        # Página principal
├── secretary_edit.php   # Formulário de edição
├── providers.php        # Gerenciamento de providers
├── providers_edit.php   # Formulário de provider
├── documents.php        # Gerenciamento de documentos
├── conversations.php    # Histórico de conversas
├── settings.php         # Configurações gerais
├── help.php             # Página de ajuda
├── resources/
│   ├── classes/         # Classes PHP do app
│   │   ├── voice_secretary.php
│   │   ├── voice_ai_provider.php
│   │   └── omniplay_api_client.php
│   ├── functions/       # Funções utilitárias
│   └── dashboard/       # Widgets de dashboard
└── languages/           # Arquivos de tradução
```

**Arquivos críticos para registro no FusionPBX**:

1. `app_config.php` - Define:
   - UUID da aplicação
   - Entradas de menu
   - Permissões (view, add, edit, delete)
   - Schema do banco de dados (tabelas v_voice_*)

2. `app_defaults.php` - Executado pelo upgrade.php para:
   - Inserir registros padrão no banco
   - Criar diretórios necessários
   - Configurar valores iniciais

**Passos de instalação**:
1. Copiar `fusionpbx-app/voice_secretary/` para `/var/www/fusionpbx/app/voice_secretary/`
2. Ajustar permissões: `chown -R www-data:www-data /var/www/fusionpbx/app/voice_secretary`
3. Ajustar modo: `chmod -R 755 /var/www/fusionpbx/app/voice_secretary`
4. Executar upgrade.php para registrar:

```bash
# Registro da aplicação (executa app_config.php e app_defaults.php)
sudo -u www-data php /var/www/fusionpbx/core/upgrade/upgrade.php

# Limpar cache do FusionPBX
sudo -u www-data rm -rf /var/cache/fusionpbx/*
```

**Verificação pós-instalação**:
```sql
-- Verificar se app está registrado
SELECT * FROM v_app_settings WHERE app_uuid = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890';

-- Verificar permissões criadas
SELECT * FROM v_group_permissions WHERE permission_name LIKE 'voice_secretary%';

-- Verificar menu criado
SELECT * FROM v_menu_items WHERE menu_item_link LIKE '%voice_secretary%';
```

### 5. Estratégia de Rollback

**Decisão**: Pontos de checkpoint com backup antes de cada fase

Fases com checkpoint:
1. **PRE_CHECK**: Antes de qualquer alteração
2. **DATABASE**: Backup do banco antes de migrations
3. **APP_INSTALL**: Backup do diretório /app se existir
4. **SERVICE_INSTALL**: Registro do estado do systemd

```bash
# Estrutura de rollback
/tmp/voice-ai-install-{timestamp}/
├── checkpoint.txt           # Última fase completada
├── database_backup.sql      # Backup do banco
├── app_backup.tar.gz        # Backup do app anterior (se existia)
└── rollback.log             # Log de operações para reverter
```

### 6. Configuração do Dialplan

**Decisão**: Inserção direta na tabela `v_dialplans` do FusionPBX com XML embutido

O FusionPBX armazena dialplans exclusivamente no banco de dados PostgreSQL. Existem duas abordagens:
1. `dialplan_xml` - XML completo da extension (mais simples, usado pelo projeto atual)
2. `v_dialplan_details` - Condições e ações separadas

**Usaremos a abordagem 1** por ser mais simples e já validada no projeto.

**Estrutura da tabela `v_dialplans`**:
| Coluna | Tipo | Descrição |
|--------|------|-----------|
| dialplan_uuid | UUID | PK |
| domain_uuid | UUID | FK para v_domains (multi-tenant) |
| dialplan_name | VARCHAR | Nome único do dialplan |
| dialplan_number | VARCHAR | Número/extensão |
| dialplan_context | VARCHAR | Contexto (public, default, etc.) |
| dialplan_continue | VARCHAR | 'true' ou 'false' |
| dialplan_order | INTEGER | Ordem de execução (menor = primeiro) |
| dialplan_enabled | VARCHAR | 'true' ou 'false' |
| dialplan_description | TEXT | Descrição |
| dialplan_xml | TEXT | XML completo da extension |

**Passos de instalação**:
1. Inserir registro na tabela `v_dialplans` com XML embutido
2. Executar `fs_cli -x "reloadxml"` para recarregar configuração
3. Executar `fs_cli -x "xml_flush_cache dialplan"` para limpar cache
4. O dialplan aparecerá automaticamente na UI do FusionPBX

```sql
-- Inserção de dialplan para Voice AI (idempotente)
-- Baseado em: voice-ai-ivr/scripts/fix_dialplan_8000.sql
INSERT INTO v_dialplans (
    dialplan_uuid,
    domain_uuid,
    dialplan_name,
    dialplan_number,
    dialplan_context,
    dialplan_continue,
    dialplan_order,
    dialplan_enabled,
    dialplan_description,
    dialplan_xml
)
SELECT 
    gen_random_uuid(),
    domain_uuid,
    'voice_ai_secretary',
    '',  -- Vazio pois pode atender múltiplas extensões
    'public',
    'false',
    5,   -- Ordem baixa para executar antes do catch-all
    'true',
    'Voice AI Secretary - ESL Outbound para IA',
    '<extension name="voice_ai_secretary" continue="false">
  <condition field="${voice_secretary_uuid}" expression="^(.+)$">
    <action application="answer"/>
    <action application="set" data="voice_secretary_uuid=${voice_secretary_uuid}"/>
    <action application="socket" data="127.0.0.1:8022 async full"/>
  </condition>
</extension>'
FROM v_domains
WHERE domain_enabled = 'true'
  AND NOT EXISTS (
    SELECT 1 FROM v_dialplans 
    WHERE dialplan_name = 'voice_ai_secretary'
      AND domain_uuid = v_domains.domain_uuid
  );
```

**Sobre ESL Outbound (socket application)**:

Baseado na documentação do FreeSWITCH (Context7):
- `socket` application conecta a chamada a um servidor ESL externo
- `127.0.0.1:8022` - IP:porta do Voice AI Realtime
- `async` - Modo assíncrono (não bloqueia o canal)
- `full` - Envia todos os eventos para o socket

O Voice AI escuta na porta 8022 e recebe conexões quando o FreeSWITCH executa este dialplan.

### 7. Modos de Operação

**Decisão**: Três modos via flags de linha de comando

```bash
./install-fusionpbx.sh --install      # Instalação nova
./install-fusionpbx.sh --upgrade      # Atualização preservando configs
./install-fusionpbx.sh --uninstall    # Remoção completa
./install-fusionpbx.sh --dry-run      # Preview sem executar
./install-fusionpbx.sh --check        # Apenas verificar ambiente
```

## Risks / Trade-offs

| Risk | Impact | Mitigation |
|------|--------|------------|
| Versão incompatível do FusionPBX | Alto | Verificação de versão no início, lista de versões suportadas |
| Falha parcial durante instalação | Médio | Rollback automático, checkpoints |
| Permissões incorretas no filesystem | Médio | Verificação e correção automática |
| Conflito com app existente | Baixo | Backup antes de sobrescrever |
| PostgreSQL com configuração não-padrão | Baixo | Fallback para input manual |

## Migration Plan

N/A - Este é um novo instalador, não modifica funcionalidade existente.

## Security Considerations

1. **Senhas em logs**: Logs nunca exibem senhas, apenas `***REDACTED***`
2. **Permissões de arquivos**: `.env` com modo 600, ownership do usuário de serviço
3. **Backup de banco**: Arquivos temporários removidos após sucesso
4. **API Keys**: Nunca armazenadas em arquivos de instalação, apenas solicitadas no .env

## Referência Técnica: FreeSWITCH ESL

### Modos de Conexão ESL

O FreeSWITCH suporta dois modos de ESL (Event Socket Layer):

| Modo | Porta Padrão | Quem Inicia | Uso no Voice AI |
|------|--------------|-------------|-----------------|
| **Inbound** | 8021 | Cliente → FreeSWITCH | Enviar comandos API |
| **Outbound** | 8022 | FreeSWITCH → Servidor | Controlar chamadas |

### ESL Outbound (usado pelo Voice AI)

Quando o FreeSWITCH executa `<action application="socket" data="127.0.0.1:8022 async full"/>`:

1. FreeSWITCH conecta ao Voice AI na porta 8022
2. Envia informações da chamada (UUID, caller ID, etc.)
3. Voice AI assume controle da chamada
4. Pode executar: answer, playback, read, bridge, hangup, etc.

**Baseado na documentação Context7 (FreeSWITCH ESL C Example)**:
```c
// Servidor ESL Outbound escuta conexões do FreeSWITCH
status = esl_listen_threaded(
    "0.0.0.0",             // bind address
    8084,                  // port
    outbound_callback,     // callback function
    NULL,                  // user data
    100                    // max concurrent connections
);
```

No Python (Voice AI), equivalente:
```python
# voice-ai-service/realtime/esl/server.py
async def start_server(host: str = "0.0.0.0", port: int = 8022):
    server = await asyncio.start_server(
        handle_connection, host, port
    )
```

### ESL Inbound (para comandos API)

Usado para enviar comandos ao FreeSWITCH (ex: originate, uuid_kill):

```python
# Conexão inbound para enviar comandos
import ESL
con = ESL.ESLconnection("127.0.0.1", "8021", "ClueCon")
con.api("reloadxml")
con.api("uuid_kill", call_uuid)
```

### Verificação de Configuração ESL

```bash
# Verificar ESL Inbound (mod_event_socket)
grep -r "listen-port" /etc/freeswitch/autoload_configs/event_socket.conf.xml

# Testar conexão ESL Inbound
fs_cli -x "status"

# Verificar porta 8022 (ESL Outbound do Voice AI)
ss -tlnp | grep 8022
```

## Open Questions

1. ~~Suportar instalação via git clone ou apenas release tarball?~~ → Ambos
2. ~~Criar usuário de serviço dedicado ou usar www-data?~~ → Usuário `voiceai` dedicado
3. ~~Dialplan via XML ou via banco de dados do FusionPBX?~~ → Via banco (tabela v_dialplans)
