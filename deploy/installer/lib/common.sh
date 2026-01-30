#!/bin/bash
# =============================================================================
# Voice AI IVR - Installer Common Functions
# =============================================================================
# Funções compartilhadas para logging, cores, verificações e utilitários
# =============================================================================

# Cores ANSI
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly MAGENTA='\033[0;35m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# Configurações globais
INSTALLER_VERSION="1.0.0"
LOG_FILE="/var/log/voice-ai/install.log"
BACKUP_DIR=""
DRY_RUN=false
VERBOSE=false
FORCE=false

# =============================================================================
# Funções de Logging
# =============================================================================

init_logging() {
    local log_dir=$(dirname "$LOG_FILE")
    if [[ ! -d "$log_dir" ]]; then
        mkdir -p "$log_dir" 2>/dev/null || {
            LOG_FILE="/tmp/voice-ai-install.log"
            log_dir="/tmp"
        }
    fi
    
    # Rotacionar log se muito grande (>10MB)
    if [[ -f "$LOG_FILE" ]] && [[ $(stat -f%z "$LOG_FILE" 2>/dev/null || stat -c%s "$LOG_FILE" 2>/dev/null) -gt 10485760 ]]; then
        mv "$LOG_FILE" "${LOG_FILE}.old"
    fi
    
    echo "=== Voice AI Installer v${INSTALLER_VERSION} - $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"
}

log() {
    local level="$1"
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    
    # Mascarar senhas e tokens
    message=$(echo "$message" | sed -E 's/(password|pass|token|secret|key)=([^ ]*)/\1=***REDACTED***/gi')
    
    echo "[$timestamp] [$level] $message" >> "$LOG_FILE"
    
    if [[ "$VERBOSE" == "true" ]] || [[ "$level" != "DEBUG" ]]; then
        case "$level" in
            INFO)    echo -e "${BLUE}[INFO]${NC} $message" ;;
            SUCCESS) echo -e "${GREEN}[OK]${NC} $message" ;;
            WARN)    echo -e "${YELLOW}[WARN]${NC} $message" ;;
            ERROR)   echo -e "${RED}[ERROR]${NC} $message" ;;
            DEBUG)   [[ "$VERBOSE" == "true" ]] && echo -e "${CYAN}[DEBUG]${NC} $message" ;;
            STEP)    echo -e "${MAGENTA}[STEP]${NC} ${BOLD}$message${NC}" ;;
            DRY)     echo -e "${CYAN}[DRY-RUN]${NC} $message" ;;
        esac
    fi
}

log_info()    { log "INFO" "$@"; }
log_success() { log "SUCCESS" "$@"; }
log_warn()    { log "WARN" "$@"; }
log_error()   { log "ERROR" "$@"; }
log_debug()   { log "DEBUG" "$@"; }
log_step()    { log "STEP" "$@"; }

# Log para dry-run
log_dry() {
    if [[ "$DRY_RUN" == "true" ]]; then
        log "DRY" "Seria executado: $*"
        return 0
    fi
    return 1
}

# Executa comando ou simula em dry-run
run_cmd() {
    local cmd="$*"
    log_debug "Executando: $cmd"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        log "DRY" "$cmd"
        return 0
    fi
    
    eval "$cmd" 2>&1 | while read -r line; do
        log_debug "$line"
    done
    
    return ${PIPESTATUS[0]}
}

# =============================================================================
# Funções de Verificação
# =============================================================================

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "Este script precisa ser executado como root"
        log_info "Use: sudo $0"
        exit 1
    fi
}

check_os() {
    local os_id=""
    local os_version=""
    local supported=false
    
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        os_id="$ID"
        os_version="$VERSION_ID"
    fi
    
    case "$os_id" in
        ubuntu)
            if [[ "${os_version%%.*}" -ge 22 ]]; then
                supported=true
            fi
            ;;
        debian)
            if [[ "${os_version%%.*}" -ge 12 ]]; then
                supported=true
            fi
            ;;
    esac
    
    if [[ "$supported" != "true" ]]; then
        log_warn "Sistema operacional não testado: $os_id $os_version"
        log_warn "Sistemas suportados: Ubuntu 22.04+, Debian 12+"
        
        if [[ "$FORCE" != "true" ]]; then
            log_error "Use --force para continuar por sua conta e risco"
            exit 1
        fi
    else
        log_success "Sistema operacional: $os_id $os_version"
    fi
    
    echo "$os_id"
}

check_disk_space() {
    local required_mb="${1:-500}"
    local target_dir="${2:-/opt}"
    
    local available_mb=$(df -m "$target_dir" | awk 'NR==2 {print $4}')
    
    if [[ "$available_mb" -lt "$required_mb" ]]; then
        log_error "Espaço em disco insuficiente em $target_dir"
        log_error "Disponível: ${available_mb}MB, Necessário: ${required_mb}MB"
        return 1
    fi
    
    log_success "Espaço em disco: ${available_mb}MB disponível em $target_dir"
    return 0
}

check_command() {
    local cmd="$1"
    local package="${2:-$cmd}"
    
    if command -v "$cmd" &> /dev/null; then
        log_debug "Comando encontrado: $cmd"
        return 0
    else
        log_debug "Comando não encontrado: $cmd (pacote: $package)"
        return 1
    fi
}

check_port() {
    local port="$1"
    local description="${2:-}"
    
    if ss -tlnp | grep -q ":${port} "; then
        local process=$(ss -tlnp | grep ":${port} " | sed -E 's/.*users:\(\("([^"]+)".*/\1/')
        log_debug "Porta $port em uso por: $process"
        return 0
    else
        log_debug "Porta $port disponível"
        return 1
    fi
}

check_service() {
    local service="$1"
    
    if systemctl is-active --quiet "$service" 2>/dev/null; then
        log_debug "Serviço ativo: $service"
        return 0
    else
        log_debug "Serviço inativo ou não existe: $service"
        return 1
    fi
}

# =============================================================================
# Funções de Interação
# =============================================================================

confirm() {
    local message="$1"
    local default="${2:-n}"
    
    if [[ "$FORCE" == "true" ]]; then
        return 0
    fi
    
    local prompt
    if [[ "$default" == "y" ]]; then
        prompt="[Y/n]"
    else
        prompt="[y/N]"
    fi
    
    echo -e -n "${YELLOW}$message${NC} $prompt "
    read -r response
    
    response=${response:-$default}
    
    case "$response" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) return 1 ;;
    esac
}

prompt_value() {
    local message="$1"
    local default="$2"
    local var_name="$3"
    
    echo -e -n "${BLUE}$message${NC} [$default]: "
    read -r value
    
    value=${value:-$default}
    eval "$var_name='$value'"
}

show_banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${BOLD}Voice AI IVR - Instalador para FusionPBX${NC}                   ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   Versão: ${INSTALLER_VERSION}                                             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                              ${CYAN}║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

show_summary() {
    local status="$1"
    local duration="$2"
    
    echo ""
    echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
    
    if [[ "$status" == "success" ]]; then
        echo -e "${GREEN}${BOLD}✓ Instalação concluída com sucesso!${NC}"
    else
        echo -e "${RED}${BOLD}✗ Instalação falhou${NC}"
    fi
    
    echo -e "${CYAN}══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "Tempo total: ${duration}s"
    echo "Log completo: $LOG_FILE"
    echo ""
}

# =============================================================================
# Funções de Arquivo
# =============================================================================

backup_file() {
    local file="$1"
    local backup_dir="${BACKUP_DIR:-/tmp/voice-ai-backup-$$}"
    
    if [[ ! -d "$backup_dir" ]]; then
        mkdir -p "$backup_dir"
    fi
    
    if [[ -f "$file" ]]; then
        local backup_path="$backup_dir/$(basename "$file").$(date +%Y%m%d%H%M%S)"
        cp "$file" "$backup_path"
        log_debug "Backup criado: $backup_path"
        echo "$backup_path"
    fi
}

backup_directory() {
    local dir="$1"
    local backup_dir="${BACKUP_DIR:-/tmp/voice-ai-backup-$$}"
    
    if [[ ! -d "$backup_dir" ]]; then
        mkdir -p "$backup_dir"
    fi
    
    if [[ -d "$dir" ]]; then
        local backup_path="$backup_dir/$(basename "$dir").$(date +%Y%m%d%H%M%S).tar.gz"
        tar -czf "$backup_path" -C "$(dirname "$dir")" "$(basename "$dir")" 2>/dev/null
        log_debug "Backup criado: $backup_path"
        echo "$backup_path"
    fi
}

create_temp_dir() {
    local prefix="${1:-voice-ai-install}"
    local temp_dir=$(mktemp -d "/tmp/${prefix}.XXXXXX")
    echo "$temp_dir"
}

cleanup_temp() {
    local temp_dir="$1"
    if [[ -d "$temp_dir" ]] && [[ "$temp_dir" == /tmp/* ]]; then
        rm -rf "$temp_dir"
        log_debug "Diretório temporário removido: $temp_dir"
    fi
}

# =============================================================================
# Funções de Instalação de Pacotes
# =============================================================================

install_package() {
    local package="$1"
    
    log_dry "apt-get install -y $package" && return 0
    
    if dpkg -s "$package" &>/dev/null; then
        log_debug "Pacote já instalado: $package"
        return 0
    fi
    
    log_info "Instalando pacote: $package"
    apt-get install -y "$package" >> "$LOG_FILE" 2>&1
    
    if [[ $? -eq 0 ]]; then
        log_success "Pacote instalado: $package"
        return 0
    else
        log_error "Falha ao instalar pacote: $package"
        return 1
    fi
}

update_apt() {
    log_dry "apt-get update" && return 0
    
    log_info "Atualizando lista de pacotes..."
    apt-get update >> "$LOG_FILE" 2>&1
}

# =============================================================================
# Funções de Usuário/Grupo
# =============================================================================

create_user() {
    local username="$1"
    local home_dir="${2:-/opt/$username}"
    
    log_dry "useradd -r -s /bin/false -d $home_dir $username" && return 0
    
    if id "$username" &>/dev/null; then
        log_debug "Usuário já existe: $username"
        return 0
    fi
    
    useradd -r -s /bin/false -d "$home_dir" "$username"
    log_success "Usuário criado: $username"
}

add_user_to_group() {
    local username="$1"
    local group="$2"
    
    log_dry "usermod -aG $group $username" && return 0
    
    if groups "$username" 2>/dev/null | grep -q "\b$group\b"; then
        log_debug "Usuário $username já está no grupo $group"
        return 0
    fi
    
    usermod -aG "$group" "$username" 2>/dev/null || true
    log_debug "Usuário $username adicionado ao grupo $group"
}

# =============================================================================
# Funções de Systemd
# =============================================================================

install_systemd_unit() {
    local unit_file="$1"
    local unit_name=$(basename "$unit_file")
    
    log_dry "cp $unit_file /etc/systemd/system/$unit_name" && return 0
    
    cp "$unit_file" "/etc/systemd/system/$unit_name"
    chmod 644 "/etc/systemd/system/$unit_name"
    
    log_debug "Unit systemd instalada: $unit_name"
}

reload_systemd() {
    log_dry "systemctl daemon-reload" && return 0
    
    systemctl daemon-reload
    log_debug "Systemd recarregado"
}

enable_service() {
    local service="$1"
    
    log_dry "systemctl enable $service" && return 0
    
    systemctl enable "$service" >> "$LOG_FILE" 2>&1
    log_debug "Serviço habilitado: $service"
}

start_service() {
    local service="$1"
    
    log_dry "systemctl start $service" && return 0
    
    systemctl start "$service" >> "$LOG_FILE" 2>&1
    
    if systemctl is-active --quiet "$service"; then
        log_success "Serviço iniciado: $service"
        return 0
    else
        log_error "Falha ao iniciar serviço: $service"
        return 1
    fi
}

stop_service() {
    local service="$1"
    
    log_dry "systemctl stop $service" && return 0
    
    if systemctl is-active --quiet "$service"; then
        systemctl stop "$service" >> "$LOG_FILE" 2>&1
        log_debug "Serviço parado: $service"
    fi
}

restart_service() {
    local service="$1"
    
    log_dry "systemctl restart $service" && return 0
    
    systemctl restart "$service" >> "$LOG_FILE" 2>&1
    
    sleep 2
    
    if systemctl is-active --quiet "$service"; then
        log_success "Serviço reiniciado: $service"
        return 0
    else
        log_error "Falha ao reiniciar serviço: $service"
        return 1
    fi
}

# =============================================================================
# Funções de Timing
# =============================================================================

start_timer() {
    echo $(date +%s)
}

get_elapsed() {
    local start="$1"
    local now=$(date +%s)
    echo $((now - start))
}

# =============================================================================
# Checklist de Pré-requisitos
# =============================================================================

print_checklist_item() {
    local status="$1"
    local message="$2"
    
    if [[ "$status" == "ok" ]]; then
        echo -e "  ${GREEN}✓${NC} $message"
    elif [[ "$status" == "warn" ]]; then
        echo -e "  ${YELLOW}!${NC} $message"
    else
        echo -e "  ${RED}✗${NC} $message"
    fi
}

# =============================================================================
# Instalação de Aliases
# =============================================================================

install_aliases() {
    log_step "Instalando aliases do Voice AI..."
    
    local tools_dir="/opt/voice-ai/tools"
    local bin_dir="/usr/local/bin"
    local script_dir="$SCRIPT_DIR/tools"
    
    if log_dry "Instalar aliases em $bin_dir"; then
        return 0
    fi
    
    # Criar diretório de ferramentas
    mkdir -p "$tools_dir"
    
    # Copiar ferramentas
    for tool in voice-ai-status.sh voice-ai-logs.sh voice-ai-uninstall.sh; do
        if [[ -f "$script_dir/$tool" ]]; then
            cp "$script_dir/$tool" "$tools_dir/"
            chmod +x "$tools_dir/$tool"
        fi
    done
    
    # Criar links simbólicos
    ln -sf "$tools_dir/voice-ai-status.sh" "$bin_dir/voice-ai-status" 2>/dev/null || true
    ln -sf "$tools_dir/voice-ai-logs.sh" "$bin_dir/voice-ai-logs" 2>/dev/null || true
    ln -sf "$tools_dir/voice-ai-uninstall.sh" "$bin_dir/voice-ai-uninstall" 2>/dev/null || true
    
    # Aliases curtos
    ln -sf "$tools_dir/voice-ai-status.sh" "$bin_dir/vai-status" 2>/dev/null || true
    ln -sf "$tools_dir/voice-ai-logs.sh" "$bin_dir/vai-logs" 2>/dev/null || true
    
    log_success "Aliases instalados: voice-ai-status, voice-ai-logs, vai-status, vai-logs"
    return 0
}
