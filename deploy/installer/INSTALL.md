# Voice AI IVR - Guia de Instalação para FusionPBX

Este guia descreve como instalar o Voice AI IVR em servidores que já possuem FusionPBX instalado.

## Requisitos

### Sistema Operacional
- Ubuntu 22.04 LTS ou superior
- Ubuntu 24.04 LTS
- Debian 12 (Bookworm) ou superior

### Software Pré-instalado
- **FusionPBX 5.x** - Instalado e funcional
- **FreeSWITCH** - Rodando com mod_event_socket ativo
- **PostgreSQL** - Banco de dados do FusionPBX
- **Python 3.11+** - Será instalado automaticamente se não existir

### Hardware Mínimo
- 2 vCPUs
- 4 GB RAM
- 20 GB de espaço em disco livre

### Acesso Necessário
- Acesso root ao servidor
- Credenciais do banco de dados PostgreSQL (detectadas automaticamente)

## Instalação Rápida

```bash
# 1. Clonar o repositório
git clone https://github.com/seu-usuario/voice-ai-ivr.git
cd voice-ai-ivr

# 2. Executar o instalador
sudo ./deploy/installer/install-fusionpbx.sh
```

## Instalação Passo a Passo

### 1. Preparar o Servidor

```bash
# Atualizar o sistema
sudo apt update && sudo apt upgrade -y

# Verificar se FreeSWITCH está rodando
sudo systemctl status freeswitch

# Verificar se FusionPBX está acessível
curl -I http://localhost/app/login
```

### 2. Baixar o Voice AI IVR

```bash
# Opção A: Via Git
git clone https://github.com/seu-usuario/voice-ai-ivr.git
cd voice-ai-ivr

# Opção B: Via download direto
wget https://github.com/seu-usuario/voice-ai-ivr/archive/main.tar.gz
tar -xzf main.tar.gz
cd voice-ai-ivr-main
```

### 3. Executar Verificação de Ambiente

```bash
# Verificar se o ambiente está pronto
sudo ./deploy/installer/install-fusionpbx.sh --check
```

O verificador irá mostrar:
- ✓ FusionPBX encontrado
- ✓ FreeSWITCH rodando
- ✓ PostgreSQL acessível
- ✓ Python 3.11+ disponível
- ✓ Espaço em disco suficiente

### 4. Executar Preview (Opcional)

```bash
# Ver o que será instalado sem fazer alterações
sudo ./deploy/installer/install-fusionpbx.sh --dry-run
```

### 5. Executar Instalação

```bash
# Instalação completa
sudo ./deploy/installer/install-fusionpbx.sh --install
```

A instalação irá:
1. Criar backup do banco de dados
2. Executar migrations (criar tabelas)
3. Instalar app no FusionPBX
4. Configurar serviço Python
5. Criar dialplans
6. Verificar instalação

### 6. Configurar API Keys

```bash
# Editar configuração
sudo nano /opt/voice-ai/.env

# Configurar pelo menos:
OPENAI_API_KEY=sk-sua-chave-aqui
```

### 7. Iniciar o Serviço

```bash
# Iniciar
sudo systemctl start voice-ai-realtime

# Verificar status
sudo systemctl status voice-ai-realtime

# Ou usar o comando de status
voice-ai-status
```

### 8. Acessar o FusionPBX

1. Acesse o FusionPBX no navegador
2. Vá em **Apps > Secretária Virtual**
3. Crie sua primeira secretária

## Opções do Instalador

```bash
# Instalação nova
sudo ./install-fusionpbx.sh --install

# Atualização (preserva configurações)
sudo ./install-fusionpbx.sh --upgrade

# Verificar ambiente
sudo ./install-fusionpbx.sh --check

# Preview sem executar
sudo ./install-fusionpbx.sh --dry-run

# Forçar instalação mesmo com avisos
sudo ./install-fusionpbx.sh --force

# Log detalhado
sudo ./install-fusionpbx.sh --verbose

# Pular criação de dialplans
sudo ./install-fusionpbx.sh --skip-dialplan

# Especificar caminho do FusionPBX
sudo ./install-fusionpbx.sh --fusionpbx-path=/caminho/custom
```

## Comandos Úteis Pós-Instalação

```bash
# Status do sistema
voice-ai-status

# Ver logs em tempo real
voice-ai-logs

# Ver apenas erros
voice-ai-logs -e

# Reiniciar serviço
sudo systemctl restart voice-ai-realtime

# Ver logs do systemd
journalctl -u voice-ai-realtime -f
```

## Estrutura de Arquivos

Após a instalação:

```
/opt/voice-ai/              # Código Python
├── .env                    # Configuração (API keys)
├── venv/                   # Virtual environment
├── realtime/               # Código do serviço
└── logs -> /var/log/voice-ai

/var/www/fusionpbx/app/
└── voice_secretary/        # App PHP do FusionPBX

/etc/systemd/system/
└── voice-ai-realtime.service

/var/log/voice-ai/          # Logs
```

## Portas Utilizadas

| Porta | Uso | Acesso |
|-------|-----|--------|
| 8021 | ESL Inbound (FreeSWITCH) | Apenas localhost |
| 8022 | ESL Outbound (Voice AI) | Apenas localhost |
| 8085 | API HTTP | Apenas localhost |

⚠️ **IMPORTANTE**: Estas portas NÃO devem ser expostas para a internet.

## Desinstalação

```bash
# Desinstalação interativa
sudo voice-ai-uninstall

# Ou via script
sudo ./deploy/installer/tools/voice-ai-uninstall.sh
```

## Próximos Passos

1. [Configurar sua primeira secretária](docs/QUICKSTART.md)
2. [Configurar providers de IA](docs/PROVIDERS.md)
3. [Solução de problemas](TROUBLESHOOTING.md)

## Suporte

- Documentação: [docs/](docs/)
- Issues: [GitHub Issues](https://github.com/seu-usuario/voice-ai-ivr/issues)
