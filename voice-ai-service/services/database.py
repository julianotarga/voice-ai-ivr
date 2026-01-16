"""
Database service for Voice AI.

Handles loading provider configurations from PostgreSQL.

⚠️ MULTI-TENANT: ALL queries MUST filter by domain_uuid.

Best Practices (from asyncpg docs):
- Use async with pool.acquire() for connection management
- Pool handles connection lifecycle automatically
- Prepared statements remain valid after release
"""

from __future__ import annotations

import os
from typing import Optional, List, Dict, Any
from uuid import UUID
import json
import structlog

import asyncpg
from asyncpg import Pool
from asyncpg.exceptions import PostgresError

from config.settings import settings

logger = structlog.get_logger()


class DatabaseService:
    """
    Async database service for PostgreSQL.
    
    ⚠️ MULTI-TENANT: All methods require domain_uuid parameter.
    
    Uses asyncpg connection pool with proper lifecycle management.
    """
    
    _pool: Optional[Pool] = None
    
    @classmethod
    async def get_pool(cls) -> Pool:
        """
        Get or create connection pool.
        
        Pool configuration follows asyncpg best practices:
        - min_size: Minimum connections to keep alive
        - max_size: Maximum connections allowed
        - max_inactive_connection_lifetime: Close idle connections
        """
        if cls._pool is None:
            try:
                cls._pool = await asyncpg.create_pool(
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    user=settings.DB_USER,
                    password=settings.DB_PASS,
                    database=settings.DB_NAME,
                    min_size=2,
                    max_size=10,
                    max_inactive_connection_lifetime=300.0,  # 5 min idle timeout
                    command_timeout=60,  # Query timeout
                )
                logger.info(
                    "Database pool created",
                    host=settings.DB_HOST,
                    database=settings.DB_NAME,
                )
            except Exception as e:
                # NOTE: asyncpg can raise PostgresError subclasses OR OSError/TimeoutError etc.
                logger.error(
                    "Failed to create database pool",
                    error=str(e),
                    error_type=type(e).__name__,
                    error_repr=repr(e),
                    host=settings.DB_HOST,
                    port=settings.DB_PORT,
                    database=settings.DB_NAME,
                    user=settings.DB_USER,
                )
                raise
        return cls._pool
    
    @classmethod
    async def close_pool(cls):
        """Close connection pool gracefully."""
        if cls._pool:
            await cls._pool.close()
            cls._pool = None
            logger.info("Database pool closed")
    
    @classmethod
    async def get_provider_config(
        cls,
        domain_uuid: UUID,
        provider_type: str,
        provider_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get provider configuration from database.
        
        Args:
            domain_uuid: REQUIRED - Domain UUID for multi-tenant isolation
            provider_type: Provider type (stt, tts, llm, embeddings)
            provider_name: Optional - Specific provider name, otherwise returns default
            
        Returns:
            Provider configuration dict or None
        """
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        pool = await cls.get_pool()
        
        if provider_name:
            # Get specific provider
            query = """
                SELECT voice_ai_provider_uuid, provider_name, config
                FROM v_voice_ai_providers
                WHERE domain_uuid = $1
                  AND provider_type = $2
                  AND provider_name = $3
                  AND is_enabled = true
                LIMIT 1
            """
            row = await pool.fetchrow(query, domain_uuid, provider_type, provider_name)
        else:
            # Get default provider for this type
            query = """
                SELECT voice_ai_provider_uuid, provider_name, config
                FROM v_voice_ai_providers
                WHERE domain_uuid = $1
                  AND provider_type = $2
                  AND is_enabled = true
                ORDER BY is_default DESC, priority ASC
                LIMIT 1
            """
            row = await pool.fetchrow(query, domain_uuid, provider_type)
        
        if not row:
            return None
        
        config = row['config']
        if isinstance(config, str):
            config = json.loads(config)
        
        return {
            'provider_uuid': str(row['voice_ai_provider_uuid']),
            'provider_name': row['provider_name'],
            'config': config,
        }
    
    @classmethod
    async def get_all_providers(
        cls,
        domain_uuid: UUID,
        provider_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all enabled providers for a domain.
        
        Args:
            domain_uuid: REQUIRED - Domain UUID
            provider_type: Optional - Filter by type
            
        Returns:
            List of provider configurations
        """
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        pool = await cls.get_pool()
        
        if provider_type:
            query = """
                SELECT voice_ai_provider_uuid, provider_type, provider_name, 
                       config, is_default, priority
                FROM v_voice_ai_providers
                WHERE domain_uuid = $1
                  AND provider_type = $2
                  AND is_enabled = true
                ORDER BY provider_type, priority ASC
            """
            rows = await pool.fetch(query, domain_uuid, provider_type)
        else:
            query = """
                SELECT voice_ai_provider_uuid, provider_type, provider_name,
                       config, is_default, priority
                FROM v_voice_ai_providers
                WHERE domain_uuid = $1
                  AND is_enabled = true
                ORDER BY provider_type, priority ASC
            """
            rows = await pool.fetch(query, domain_uuid)
        
        providers = []
        for row in rows:
            config = row['config']
            if isinstance(config, str):
                config = json.loads(config)
            
            providers.append({
                'provider_uuid': str(row['voice_ai_provider_uuid']),
                'provider_type': row['provider_type'],
                'provider_name': row['provider_name'],
                'config': config,
                'is_default': row['is_default'],
                'priority': row['priority'],
            })
        
        return providers
    
    @classmethod
    async def get_secretary_config(
        cls,
        domain_uuid: UUID,
        secretary_uuid: Optional[UUID] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get secretary configuration.
        
        Args:
            domain_uuid: REQUIRED - Domain UUID
            secretary_uuid: Optional - Specific secretary, otherwise returns first enabled
            
        Returns:
            Secretary configuration dict or None
        """
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        pool = await cls.get_pool()
        
        if secretary_uuid:
            query = """
                SELECT * FROM v_voice_secretaries
                WHERE domain_uuid = $1
                  AND voice_secretary_uuid = $2
                  AND enabled = true
                LIMIT 1
            """
            row = await pool.fetchrow(query, domain_uuid, secretary_uuid)
        else:
            query = """
                SELECT * FROM v_voice_secretaries
                WHERE domain_uuid = $1
                  AND enabled = true
                ORDER BY insert_date ASC
                LIMIT 1
            """
            row = await pool.fetchrow(query, domain_uuid)
        
        if not row:
            return None
        
        return dict(row)
    
    @classmethod
    async def get_domain_settings(
        cls,
        domain_uuid: UUID,
        setting_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get global domain settings from v_voice_secretary_settings.
        
        These are the settings configured in FusionPBX settings.php page.
        
        Args:
            domain_uuid: REQUIRED - Domain UUID
            setting_names: Optional - List of specific settings to fetch
            
        Returns:
            Dict of setting_name -> setting_value with defaults applied
        """
        if not domain_uuid:
            raise ValueError("domain_uuid is required for multi-tenant isolation")
        
        # Default values - must match settings.php $defaults
        defaults = {
            # Service Configuration
            # NOTA: URL base SEM /api/v1 - o prefixo é adicionado pelos endpoints
            'service_url': 'http://127.0.0.1:8100',
            'max_concurrent_calls': 10,
            'default_max_turns': 20,
            'rate_limit_rpm': 60,
            
            # ESL Configuration (FreeSWITCH)
            'esl_host': '127.0.0.1',
            'esl_port': 8021,
            'esl_password': 'ClueCon',
            'esl_connect_timeout': 5.0,
            'esl_read_timeout': 30.0,
            
            # Transfer Settings
            'transfer_default_timeout': 30,
            'transfer_announce_enabled': True,
            'transfer_music_on_hold': 'local_stream://moh',
            'transfer_cache_ttl_seconds': 300,
            
            # Callback Settings
            'callback_enabled': True,
            'callback_expiration_hours': 24,
            'callback_max_notifications': 5,
            'callback_min_interval_minutes': 10,
            
            # OmniPlay Integration
            'omniplay_api_url': 'http://127.0.0.1:8080',
            'omniplay_api_timeout_ms': 10000,
            'omniplay_api_key': '',
            'omniplay_webhook_url': '',
            
            # Data Management
            'data_retention_days': 90,
            'recording_enabled': True,
            
            # Audio Settings
            'audio_sample_rate': 16000,
            'silence_threshold_ms': 3000,
            'max_recording_seconds': 30,
        }
        
        pool = await cls.get_pool()
        
        if setting_names:
            # Fetch specific settings
            query = """
                SELECT setting_name, setting_value 
                FROM v_voice_secretary_settings
                WHERE domain_uuid = $1
                  AND setting_name = ANY($2)
            """
            rows = await pool.fetch(query, domain_uuid, setting_names)
        else:
            # Fetch all settings
            query = """
                SELECT setting_name, setting_value 
                FROM v_voice_secretary_settings
                WHERE domain_uuid = $1
            """
            rows = await pool.fetch(query, domain_uuid)
        
        # Start with defaults
        result = defaults.copy()
        
        # Override with database values
        for row in rows:
            name = row['setting_name']
            value = row['setting_value']
            
            if name in result:
                # Convert to appropriate type based on default
                default_type = type(defaults.get(name, value))
                try:
                    if default_type == bool:
                        result[name] = value.lower() in ('true', '1', 'yes')
                    elif default_type == int:
                        result[name] = int(value)
                    elif default_type == float:
                        result[name] = float(value)
                    else:
                        result[name] = value
                except (ValueError, AttributeError):
                    result[name] = value
            else:
                result[name] = value
        
        return result
    
    @classmethod
    async def get_setting(
        cls,
        domain_uuid: UUID,
        setting_name: str,
        default: Any = None,
    ) -> Any:
        """
        Get a single domain setting.
        
        Args:
            domain_uuid: REQUIRED - Domain UUID
            setting_name: Name of the setting
            default: Default value if not found
            
        Returns:
            Setting value or default
        """
        settings = await cls.get_domain_settings(domain_uuid, [setting_name])
        return settings.get(setting_name, default)


# Singleton instance
db = DatabaseService()
