# Voice AI IVR - Guia de Solução de Problemas

Este guia ajuda a diagnosticar e resolver problemas comuns na instalação e operação do Voice AI IVR.

## Diagnóstico Rápido

```bash
# Verificar status completo
voice-ai-status

# Ver logs recentes
voice-ai-logs -n 50

# Ver apenas erros
voice-ai-logs -e

# Status detalhado do serviço
systemctl status voice-ai-realtime
```

---

## Problemas de Instalação

### FusionPBX não encontrado

**Sintoma:**
```
[ERROR] FusionPBX não encontrado
```

**Soluções:**
1. Verificar se o FusionPBX está instalado:
   ```bash
   ls -la /var/www/fusionpbx/
   ```

2. Especificar caminho customizado:
   ```bash
   sudo ./install-fusionpbx.sh --fusionpbx-path=/caminho/para/fusionpbx
   ```

3. Verificar se config.php existe:
   ```bash
   ls -la /var/www/fusionpbx/resources/config.php
   ```

---

### FreeSWITCH não está rodando

**Sintoma:**
```
[ERROR] FreeSWITCH não está rodando
```

**Soluções:**
1. Iniciar o FreeSWITCH:
   ```bash
   sudo systemctl start freeswitch
   ```

2. Verificar logs do FreeSWITCH:
   ```bash
   cat /var/log/freeswitch/freeswitch.log | tail -50
   ```

3. Verificar se ESL está habilitado:
   ```bash
   fs_cli -x "module_exists mod_event_socket"
   ```

---

### ESL não acessível na porta 8021

**Sintoma:**
```
[ERROR] ESL não acessível na porta 8021
```

**Soluções:**
1. Verificar se mod_event_socket está carregado:
   ```bash
   fs_cli -x "load mod_event_socket"
   ```

2. Verificar configuração do event_socket:
   ```bash
   cat /etc/freeswitch/autoload_configs/event_socket.conf.xml
   ```

3. Verificar se porta está escutando:
   ```bash
   ss -tlnp | grep 8021
   ```

4. Testar conexão manualmente:
   ```bash
   nc -z 127.0.0.1 8021 && echo "OK" || echo "FALHA"
   ```

---

### Python 3.11+ não encontrado

**Sintoma:**
```
[WARN] Python 3.11+ não encontrado
```

**Soluções:**
1. Instalar Python 3.11 (Ubuntu):
   ```bash
   sudo add-apt-repository ppa:deadsnakes/ppa
   sudo apt update
   sudo apt install python3.11 python3.11-venv python3.11-dev
   ```

2. Instalar Python 3.11 (Debian):
   ```bash
   sudo apt update
   sudo apt install python3.11 python3.11-venv
   ```

---

### Falha na conexão com banco de dados

**Sintoma:**
```
[ERROR] Falha na conexão com o banco de dados
```

**Soluções:**
1. Verificar se PostgreSQL está rodando:
   ```bash
   sudo systemctl status postgresql
   ```

2. Testar conexão manualmente:
   ```bash
   sudo -u postgres psql -d fusionpbx -c "SELECT 1"
   ```

3. Verificar credenciais em config.php:
   ```bash
   grep -E "db_host|db_name|db_user" /var/www/fusionpbx/resources/config.php
   ```

---

## Problemas de Operação

### Serviço não inicia

**Sintoma:**
```bash
$ systemctl status voice-ai-realtime
● voice-ai-realtime.service - Voice AI Realtime Bridge
   Active: failed
```

**Soluções:**
1. Ver logs detalhados:
   ```bash
   journalctl -u voice-ai-realtime -n 100 --no-pager
   ```

2. Verificar se .env existe e está configurado:
   ```bash
   cat /opt/voice-ai/.env | grep -v "^#" | grep -v "^$"
   ```

3. Verificar permissões:
   ```bash
   ls -la /opt/voice-ai/.env
   # Deve ser: -rw------- voiceai voiceai
   ```

4. Testar manualmente:
   ```bash
   cd /opt/voice-ai
   source venv/bin/activate
   python -m realtime
   ```

---

### Porta 8022 não está escutando

**Sintoma:**
```bash
$ ss -tlnp | grep 8022
# (vazio)
```

**Soluções:**
1. Verificar se serviço está rodando:
   ```bash
   systemctl status voice-ai-realtime
   ```

2. Verificar logs de erro:
   ```bash
   journalctl -u voice-ai-realtime | grep -i error | tail -20
   ```

3. Verificar se porta não está em uso por outro processo:
   ```bash
   sudo lsof -i :8022
   ```

---

### "insufficient_quota" da OpenAI

**Sintoma:**
```
[ERROR] Response FAILED: insufficient_quota - You exceeded your current quota
```

**Soluções:**
1. Verificar créditos na OpenAI:
   - Acesse: https://platform.openai.com/account/billing

2. Adicionar créditos ou atualizar plano

3. Verificar se API key está correta:
   ```bash
   curl https://api.openai.com/v1/models \
     -H "Authorization: Bearer $(grep OPENAI_API_KEY /opt/voice-ai/.env | cut -d= -f2)"
   ```

---

### Chamadas não são roteadas para Voice AI

**Sintoma:**
- Chamadas para a extensão configurada não chegam ao Voice AI
- FreeSWITCH não conecta na porta 8022

**Soluções:**
1. Verificar dialplan no banco:
   ```bash
   sudo -u postgres psql -d fusionpbx -c \
     "SELECT dialplan_name, dialplan_enabled FROM v_dialplans WHERE dialplan_name LIKE '%voice_ai%'"
   ```

2. Verificar se dialplan está habilitado:
   ```sql
   UPDATE v_dialplans SET dialplan_enabled = 'true' 
   WHERE dialplan_name LIKE '%voice_ai%';
   ```

3. Recarregar dialplan:
   ```bash
   fs_cli -x "reloadxml"
   fs_cli -x "xml_flush_cache dialplan"
   ```

4. Verificar ordem do dialplan (deve ser baixa, ex: 5):
   ```bash
   sudo -u postgres psql -d fusionpbx -c \
     "SELECT dialplan_name, dialplan_order FROM v_dialplans WHERE dialplan_name LIKE '%voice%' ORDER BY dialplan_order"
   ```

---

### Sem áudio na chamada

**Sintoma:**
- Chamada conecta mas não há áudio
- IA não responde

**Soluções:**
1. Verificar logs do Voice AI:
   ```bash
   voice-ai-logs | grep -i "audio\|response"
   ```

2. Verificar se OpenAI Realtime está respondendo:
   ```bash
   voice-ai-logs | grep -i "openai\|response.done"
   ```

3. Verificar configuração de áudio em .env:
   ```bash
   grep -i audio /opt/voice-ai/.env
   ```

4. Verificar créditos da OpenAI (ver seção acima)

---

### App não aparece no menu do FusionPBX

**Sintoma:**
- Após instalação, menu "Secretária Virtual" não aparece

**Soluções:**
1. Executar upgrade.php:
   ```bash
   sudo -u www-data php /var/www/fusionpbx/core/upgrade/upgrade.php
   ```

2. Limpar cache:
   ```bash
   sudo rm -rf /var/cache/fusionpbx/*
   ```

3. Verificar permissões:
   ```bash
   ls -la /var/www/fusionpbx/app/voice_secretary/
   # Deve ser: www-data:www-data
   ```

4. Verificar se app_config.php existe:
   ```bash
   ls -la /var/www/fusionpbx/app/voice_secretary/app_config.php
   ```

---

## Logs Importantes

### Localização dos Logs

| Log | Caminho |
|-----|---------|
| Voice AI | `/var/log/voice-ai/` |
| Systemd | `journalctl -u voice-ai-realtime` |
| FreeSWITCH | `/var/log/freeswitch/freeswitch.log` |
| PostgreSQL | `/var/log/postgresql/` |
| Instalador | `/var/log/voice-ai/install.log` |

### Filtrar Logs por Componente

```bash
# Eventos ESL
voice-ai-logs -s "ESL"

# Erros OpenAI
voice-ai-logs -s "OPENAI" -e

# Estado da chamada
voice-ai-logs -s "STATE_MACHINE"

# Heartbeat/saúde
voice-ai-logs -s "HEARTBEAT"
```

---

## Comandos de Diagnóstico

```bash
# Status completo
voice-ai-status

# Testar conexão ESL
echo -e "auth ClueCon\napi status\nexit" | nc 127.0.0.1 8021

# Testar banco de dados
sudo -u postgres psql -d fusionpbx -c "SELECT COUNT(*) FROM v_voice_secretaries"

# Verificar portas
ss -tlnp | grep -E "8021|8022|8085"

# Verificar processos
ps aux | grep voice-ai

# Uso de memória
free -h

# Espaço em disco
df -h /opt/voice-ai
```

---

## Rollback / Recuperação

### Restaurar Banco de Dados

Se algo deu errado durante a instalação:

```bash
# Encontrar backup
ls -la /tmp/voice-ai-rollback-*/backups/

# Restaurar
PGPASSWORD=senha psql -h localhost -U fusionpbx -d fusionpbx < /tmp/voice-ai-rollback-*/backups/database_*.sql
```

### Desinstalar Completamente

```bash
sudo voice-ai-uninstall
```

### Reinstalar do Zero

```bash
# Desinstalar
sudo voice-ai-uninstall

# Reinstalar
sudo ./deploy/installer/install-fusionpbx.sh --install --force
```

---

## Suporte

Se o problema persistir:

1. Colete logs:
   ```bash
   voice-ai-status > /tmp/voice-ai-debug.txt
   voice-ai-logs -n 200 >> /tmp/voice-ai-debug.txt
   journalctl -u voice-ai-realtime -n 500 --no-pager >> /tmp/voice-ai-debug.txt
   ```

2. Abra uma issue no GitHub com:
   - Descrição do problema
   - Passos para reproduzir
   - Logs coletados
   - Versão do sistema operacional
   - Versão do FusionPBX
