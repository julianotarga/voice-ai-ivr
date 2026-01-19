# Voice AI Realtime - G.711 Native Support

## ADDED Requirements

### Requirement: G.711 Native Audio Format

O sistema DEVE suportar G.711 μ-law (PCMU) como formato de áudio nativo para comunicação com a OpenAI Realtime API.

#### Scenario: Configuração G.711 ativada
- **GIVEN** uma secretária com `audio_format = "g711"`
- **WHEN** uma chamada é iniciada
- **THEN** o mod_audio_stream DEVE ser configurado com formato `mulaw`
- **AND** o session.update da OpenAI DEVE usar `audio/pcmu`
- **AND** nenhum resample DEVE ser realizado

#### Scenario: Fallback para PCM16
- **GIVEN** uma secretária com `audio_format = "pcm16"` (ou não configurado)
- **WHEN** uma chamada é iniciada
- **THEN** o comportamento atual DEVE ser mantido (PCM16 16kHz com resample)

#### Scenario: Latência reduzida
- **GIVEN** uma chamada usando G.711 nativo
- **WHEN** áudio é transmitido
- **THEN** a latência de processamento de áudio DEVE ser < 5ms (sem resample)

### Requirement: Echo Cancellation com G.711

O Echo Canceller DEVE funcionar corretamente com áudio G.711 8kHz.

#### Scenario: AEC com G.711
- **GIVEN** uma chamada usando G.711
- **WHEN** o caller usa viva-voz
- **THEN** o eco DEVE ser removido antes de enviar à OpenAI
- **AND** a conversão G.711↔PCM16 DEVE ser feita apenas para o processamento AEC

### Requirement: Barge-in Detection com G.711

O sistema de detecção de interrupção DEVE funcionar com áudio G.711.

#### Scenario: Barge-in com G.711
- **GIVEN** uma chamada usando G.711
- **WHEN** o caller interrompe o agente
- **THEN** a interrupção DEVE ser detectada
- **AND** o threshold de RMS DEVE ser ajustado para 8-bit audio

## MODIFIED Requirements

### Requirement: Audio Format Configuration

O sistema DEVE permitir configurar o formato de áudio por secretária.

#### Scenario: Configuração de formato
- **GIVEN** uma secretária no FusionPBX
- **WHEN** o administrador configura `audio_format`
- **THEN** os valores válidos DEVEM ser: `g711`, `pcm16`
- **AND** o padrão DEVE ser `g711` (para novos deployments)

#### Scenario: Migração de secretárias existentes
- **GIVEN** secretárias existentes sem `audio_format` configurado
- **WHEN** o sistema é atualizado
- **THEN** o comportamento padrão DEVE ser `pcm16` para compatibilidade
