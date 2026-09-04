"""Normalized, schema-qualified PostgreSQL collaboration schema with non-recursive RLS."""
from __future__ import annotations

MIGRATIONS_TABLE_DDL = """
CREATE SCHEMA IF NOT EXISTS nanobot_collaboration;
REVOKE CREATE ON SCHEMA nanobot_collaboration FROM PUBLIC;
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    applied_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
"""

_BASE_SCHEMA_DDL = r"""
CREATE SCHEMA IF NOT EXISTS nanobot_collaboration;
REVOKE CREATE ON SCHEMA nanobot_collaboration FROM PUBLIC;

CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    applied_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_users (
    id varchar(128) PRIMARY KEY,
    display_name varchar(256) NOT NULL CHECK (length(btrim(display_name)) > 0),
    default_project_id varchar(128), default_vault_id varchar(128), default_persona_id varchar(128),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms)
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_organizations (
    id varchar(128) PRIMARY KEY, name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0),
    is_personal boolean NOT NULL DEFAULT false,
    created_by_user_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_users(id) ON DELETE RESTRICT,
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (id, created_by_user_id)
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_organization_memberships (
    organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    user_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_users(id) ON DELETE RESTRICT,
    role varchar(16) NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), PRIMARY KEY (organization_id, user_id)
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_identities (
    organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    user_id varchar(128) NOT NULL, channel varchar(128) NOT NULL CHECK (length(btrim(channel)) > 0),
    sender_id varchar(512) NOT NULL CHECK (length(btrim(sender_id)) > 0), created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (channel, sender_id),
    FOREIGN KEY (organization_id, user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_vaults (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    owner_user_id varchar(128) NOT NULL, name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0),
    kind varchar(16) NOT NULL CHECK (kind IN ('private', 'work', 'life')),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, owner_user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_personas (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    owner_user_id varchar(128) NOT NULL, name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0), default_vault_id varchar(128) NOT NULL,
    instructions varchar(32768) NOT NULL DEFAULT '', created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, owner_user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, default_vault_id) REFERENCES nanobot_collaboration.collaboration_vaults (organization_id, id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_share_grants (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    vault_id varchar(128) NOT NULL, grantee_user_id varchar(128) NOT NULL,
    resource_type varchar(128) NOT NULL CHECK (length(btrim(resource_type)) > 0), resource_id varchar(128),
    permission varchar(16) NOT NULL CHECK (permission IN ('read', 'collaborate')),
    expires_at_ms bigint CHECK (expires_at_ms IS NULL OR expires_at_ms >= 0), created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    revoked_at_ms bigint CHECK (revoked_at_ms IS NULL OR revoked_at_ms >= created_at_ms), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, vault_id) REFERENCES nanobot_collaboration.collaboration_vaults (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, grantee_user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_projects (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE CASCADE,
    name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0), workspace_path varchar(4096) NOT NULL CHECK (length(btrim(workspace_path)) > 0),
    created_by_user_id varchar(128) NOT NULL, created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (organization_id, id), FOREIGN KEY (organization_id, created_by_user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_project_memberships (
    organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL,
    role varchar(16) NOT NULL CHECK (role IN ('owner', 'member')), created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (organization_id, project_id, user_id),
    FOREIGN KEY (organization_id, project_id) REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_task_lists (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL,
    name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0), position integer NOT NULL CHECK (position >= 0),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (organization_id, id), UNIQUE (organization_id, id, project_id),
    FOREIGN KEY (organization_id, project_id) REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_project_tasks (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL, task_list_id varchar(128) NOT NULL,
    title varchar(512) NOT NULL CHECK (length(btrim(title)) > 0), status varchar(16) NOT NULL CHECK (status IN ('todo', 'in_progress', 'done', 'cancelled')),
    description varchar(65536) NOT NULL DEFAULT '', assignee_user_id varchar(128), position integer NOT NULL CHECK (position >= 0),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, task_list_id, project_id) REFERENCES nanobot_collaboration.collaboration_task_lists (organization_id, id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, project_id, assignee_user_id) REFERENCES nanobot_collaboration.collaboration_project_memberships (organization_id, project_id, user_id) ON DELETE SET NULL (assignee_user_id)
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_personal_tasks (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, owner_user_id varchar(128) NOT NULL, vault_id varchar(128) NOT NULL,
    title varchar(512) NOT NULL CHECK (length(btrim(title)) > 0), note varchar(65536) NOT NULL DEFAULT '',
    status varchar(16) NOT NULL CHECK (status IN ('todo', 'in_progress', 'done', 'cancelled')), priority integer NOT NULL DEFAULT 0 CHECK (priority BETWEEN -100 AND 100),
    due_at_ms bigint CHECK (due_at_ms IS NULL OR due_at_ms >= 0), timezone varchar(128), recurrence_rule varchar(2048),
    source_type varchar(128) NOT NULL DEFAULT '' CHECK (length(source_type) <= 128), source_ref varchar(2048), external_provider varchar(128), external_id varchar(512), external_version varchar(512),
    review_state varchar(16) NOT NULL CHECK (review_state IN ('confirmed', 'proposed', 'dismissed')),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, owner_user_id) REFERENCES nanobot_collaboration.collaboration_organization_memberships (organization_id, user_id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, vault_id) REFERENCES nanobot_collaboration.collaboration_vaults (organization_id, id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS collaboration_personal_tasks_external_identity ON nanobot_collaboration.collaboration_personal_tasks (organization_id, external_provider, external_id) WHERE external_provider IS NOT NULL AND external_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_conversation_bindings (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, channel varchar(128) NOT NULL CHECK (length(btrim(channel)) > 0),
    conversation_id varchar(512) NOT NULL CHECK (length(btrim(conversation_id)) > 0), thread_id varchar(512) NOT NULL DEFAULT '', project_id varchar(128) NOT NULL,
    created_by_user_id varchar(128) NOT NULL, created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (organization_id, id), UNIQUE (channel, conversation_id, thread_id),
    FOREIGN KEY (organization_id, project_id, created_by_user_id) REFERENCES nanobot_collaboration.collaboration_project_memberships (organization_id, project_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_extension_profiles (
    organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL,
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0), settings jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_catalog.pg_column_size(settings) <= 65536),
    updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= 0), PRIMARY KEY (organization_id, project_id, user_id),
    FOREIGN KEY (organization_id, project_id, user_id) REFERENCES nanobot_collaboration.collaboration_project_memberships (organization_id, project_id, user_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_context_sources (
    id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL,
    name varchar(256) NOT NULL CHECK (length(btrim(name)) > 0), kind varchar(16) NOT NULL CHECK (kind IN ('skill', 'mcp', 'plugin', 'document', 'custom')),
    enabled boolean NOT NULL DEFAULT true, config jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (pg_catalog.pg_column_size(config) <= 65536),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0), updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms), UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, project_id) REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE
);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'collaboration_users_default_project_fk'
          AND conrelid = 'nanobot_collaboration.collaboration_users'::pg_catalog.regclass
    ) THEN
        ALTER TABLE nanobot_collaboration.collaboration_users
            ADD CONSTRAINT collaboration_users_default_project_fk
            FOREIGN KEY (default_project_id)
            REFERENCES nanobot_collaboration.collaboration_projects(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'collaboration_users_default_vault_fk'
          AND conrelid = 'nanobot_collaboration.collaboration_users'::pg_catalog.regclass
    ) THEN
        ALTER TABLE nanobot_collaboration.collaboration_users
            ADD CONSTRAINT collaboration_users_default_vault_fk
            FOREIGN KEY (default_vault_id)
            REFERENCES nanobot_collaboration.collaboration_vaults(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'collaboration_users_default_persona_fk'
          AND conrelid = 'nanobot_collaboration.collaboration_users'::pg_catalog.regclass
    ) THEN
        ALTER TABLE nanobot_collaboration.collaboration_users
            ADD CONSTRAINT collaboration_users_default_persona_fk
            FOREIGN KEY (default_persona_id)
            REFERENCES nanobot_collaboration.collaboration_personas(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- Authorization projections are not RLS-protected business tables.  Only fixed-path
-- definer functions below can read them, and their direct privileges are revoked.
CREATE TABLE IF NOT EXISTS nanobot_collaboration.organization_creator_edges (organization_id varchar(128) PRIMARY KEY, creator_user_id varchar(128) NOT NULL);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.organization_role_edges (organization_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL, role varchar(16) NOT NULL, PRIMARY KEY (organization_id, user_id));
CREATE TABLE IF NOT EXISTS nanobot_collaboration.project_creator_edges (organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL, creator_user_id varchar(128) NOT NULL, PRIMARY KEY (organization_id, project_id));
CREATE TABLE IF NOT EXISTS nanobot_collaboration.project_role_edges (organization_id varchar(128) NOT NULL, project_id varchar(128) NOT NULL, user_id varchar(128) NOT NULL, role varchar(16) NOT NULL, PRIMARY KEY (organization_id, project_id, user_id));
CREATE TABLE IF NOT EXISTS nanobot_collaboration.vault_owner_edges (organization_id varchar(128) NOT NULL, vault_id varchar(128) NOT NULL, owner_user_id varchar(128) NOT NULL, PRIMARY KEY (organization_id, vault_id));
CREATE TABLE IF NOT EXISTS nanobot_collaboration.vault_grant_edges (grant_id varchar(128) PRIMARY KEY, organization_id varchar(128) NOT NULL, vault_id varchar(128) NOT NULL, grantee_user_id varchar(128) NOT NULL, resource_type varchar(128) NOT NULL, resource_id varchar(128), permission varchar(16) NOT NULL, expires_at_ms bigint, revoked_at_ms bigint);

CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_current_user_id() RETURNS varchar LANGUAGE sql STABLE SET search_path = pg_catalog AS $$ SELECT NULLIF(pg_catalog.current_setting('nanobot.user_id', true), '')::varchar $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_has_organization_role(candidate_organization_id varchar, allowed_roles varchar[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT EXISTS (SELECT 1 FROM nanobot_collaboration.organization_role_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.user_id = nanobot_collaboration.nanobot_current_user_id() AND edge.role = ANY(allowed_roles)) $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_has_project_role(candidate_organization_id varchar, candidate_project_id varchar, allowed_roles varchar[]) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT EXISTS (SELECT 1 FROM nanobot_collaboration.project_role_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.project_id = candidate_project_id AND edge.user_id = nanobot_collaboration.nanobot_current_user_id() AND edge.role = ANY(allowed_roles)) $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_is_organization_creator(candidate_organization_id varchar) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT EXISTS (SELECT 1 FROM nanobot_collaboration.organization_creator_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.creator_user_id = nanobot_collaboration.nanobot_current_user_id()) $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_is_project_creator(candidate_organization_id varchar, candidate_project_id varchar) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT EXISTS (SELECT 1 FROM nanobot_collaboration.project_creator_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.project_id = candidate_project_id AND edge.creator_user_id = nanobot_collaboration.nanobot_current_user_id()) $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_is_vault_owner(candidate_organization_id varchar, candidate_vault_id varchar) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT EXISTS (SELECT 1 FROM nanobot_collaboration.vault_owner_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.vault_id = candidate_vault_id AND edge.owner_user_id = nanobot_collaboration.nanobot_current_user_id()) $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_can_access_vault_resource(candidate_organization_id varchar, candidate_vault_id varchar, candidate_resource_type varchar, candidate_resource_id varchar, require_collaborate boolean DEFAULT false) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$ SELECT nanobot_collaboration.nanobot_is_vault_owner(candidate_organization_id, candidate_vault_id) OR EXISTS (SELECT 1 FROM nanobot_collaboration.vault_grant_edges AS edge WHERE edge.organization_id = candidate_organization_id AND edge.vault_id = candidate_vault_id AND edge.grantee_user_id = nanobot_collaboration.nanobot_current_user_id() AND edge.revoked_at_ms IS NULL AND (edge.expires_at_ms IS NULL OR edge.expires_at_ms > (pg_catalog.date_part('epoch', pg_catalog.clock_timestamp()) * 1000)::bigint) AND (edge.permission = 'collaborate' OR NOT require_collaborate) AND ((edge.resource_type = 'vault' AND edge.resource_id IS NULL) OR (edge.resource_type = candidate_resource_type AND edge.resource_id = candidate_resource_id))) $$;

CREATE OR REPLACE FUNCTION nanobot_collaboration.lock_organization_owner_key() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    old_lock bigint;
    new_lock bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(NEW.organization_id, 0));
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(OLD.organization_id, 0));
        RETURN OLD;
    END IF;
    old_lock := pg_catalog.hashtextextended(OLD.organization_id, 0);
    new_lock := pg_catalog.hashtextextended(NEW.organization_id, 0);
    IF old_lock <= new_lock THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
        IF old_lock <> new_lock THEN PERFORM pg_catalog.pg_advisory_xact_lock(new_lock); END IF;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(new_lock);
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
    END IF;
    RETURN NEW;
END
$$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.lock_project_owner_key() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    old_lock bigint;
    new_lock bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(NEW.organization_id || ':' || NEW.project_id, 0));
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(OLD.organization_id || ':' || OLD.project_id, 0));
        RETURN OLD;
    END IF;
    old_lock := pg_catalog.hashtextextended(OLD.organization_id || ':' || OLD.project_id, 0);
    new_lock := pg_catalog.hashtextextended(NEW.organization_id || ':' || NEW.project_id, 0);
    IF old_lock <= new_lock THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
        IF old_lock <> new_lock THEN PERFORM pg_catalog.pg_advisory_xact_lock(new_lock); END IF;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(new_lock);
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
    END IF;
    RETURN NEW;
END
$$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_organization_creator_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP = 'DELETE' THEN DELETE FROM nanobot_collaboration.organization_creator_edges WHERE organization_id = OLD.id; RETURN OLD; END IF; IF TG_OP = 'UPDATE' THEN DELETE FROM nanobot_collaboration.organization_creator_edges WHERE organization_id = OLD.id; END IF; INSERT INTO nanobot_collaboration.organization_creator_edges (organization_id, creator_user_id) VALUES (NEW.id, NEW.created_by_user_id) ON CONFLICT (organization_id) DO UPDATE SET creator_user_id = EXCLUDED.creator_user_id; RETURN NEW; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_organization_role_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP IN ('DELETE', 'UPDATE') THEN DELETE FROM nanobot_collaboration.organization_role_edges WHERE organization_id = OLD.organization_id AND user_id = OLD.user_id; END IF; IF TG_OP <> 'DELETE' THEN INSERT INTO nanobot_collaboration.organization_role_edges (organization_id, user_id, role) VALUES (NEW.organization_id, NEW.user_id, NEW.role) ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role; RETURN NEW; END IF; RETURN OLD; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_project_creator_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP = 'DELETE' THEN DELETE FROM nanobot_collaboration.project_creator_edges WHERE organization_id = OLD.organization_id AND project_id = OLD.id; RETURN OLD; END IF; IF TG_OP = 'UPDATE' THEN DELETE FROM nanobot_collaboration.project_creator_edges WHERE organization_id = OLD.organization_id AND project_id = OLD.id; END IF; INSERT INTO nanobot_collaboration.project_creator_edges (organization_id, project_id, creator_user_id) VALUES (NEW.organization_id, NEW.id, NEW.created_by_user_id) ON CONFLICT (organization_id, project_id) DO UPDATE SET creator_user_id = EXCLUDED.creator_user_id; RETURN NEW; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_project_role_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP IN ('DELETE', 'UPDATE') THEN DELETE FROM nanobot_collaboration.project_role_edges WHERE organization_id = OLD.organization_id AND project_id = OLD.project_id AND user_id = OLD.user_id; END IF; IF TG_OP <> 'DELETE' THEN INSERT INTO nanobot_collaboration.project_role_edges (organization_id, project_id, user_id, role) VALUES (NEW.organization_id, NEW.project_id, NEW.user_id, NEW.role) ON CONFLICT (organization_id, project_id, user_id) DO UPDATE SET role = EXCLUDED.role; RETURN NEW; END IF; RETURN OLD; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_vault_owner_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP = 'DELETE' THEN DELETE FROM nanobot_collaboration.vault_owner_edges WHERE organization_id = OLD.organization_id AND vault_id = OLD.id; RETURN OLD; END IF; IF TG_OP = 'UPDATE' THEN DELETE FROM nanobot_collaboration.vault_owner_edges WHERE organization_id = OLD.organization_id AND vault_id = OLD.id; END IF; INSERT INTO nanobot_collaboration.vault_owner_edges (organization_id, vault_id, owner_user_id) VALUES (NEW.organization_id, NEW.id, NEW.owner_user_id) ON CONFLICT (organization_id, vault_id) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id; RETURN NEW; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.sync_vault_grant_edge() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF TG_OP IN ('DELETE', 'UPDATE') THEN DELETE FROM nanobot_collaboration.vault_grant_edges WHERE grant_id = OLD.id; END IF; IF TG_OP <> 'DELETE' THEN INSERT INTO nanobot_collaboration.vault_grant_edges (grant_id, organization_id, vault_id, grantee_user_id, resource_type, resource_id, permission, expires_at_ms, revoked_at_ms) VALUES (NEW.id, NEW.organization_id, NEW.vault_id, NEW.grantee_user_id, NEW.resource_type, NEW.resource_id, NEW.permission, NEW.expires_at_ms, NEW.revoked_at_ms) ON CONFLICT (grant_id) DO UPDATE SET organization_id = EXCLUDED.organization_id, vault_id = EXCLUDED.vault_id, grantee_user_id = EXCLUDED.grantee_user_id, resource_type = EXCLUDED.resource_type, resource_id = EXCLUDED.resource_id, permission = EXCLUDED.permission, expires_at_ms = EXCLUDED.expires_at_ms, revoked_at_ms = EXCLUDED.revoked_at_ms; RETURN NEW; END IF; RETURN OLD; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.reject_last_organization_owner() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF EXISTS (SELECT 1 FROM nanobot_collaboration.organization_creator_edges WHERE organization_id = OLD.organization_id) AND NOT EXISTS (SELECT 1 FROM nanobot_collaboration.organization_role_edges WHERE organization_id = OLD.organization_id AND role = 'owner') THEN RAISE EXCEPTION 'an organization must retain an owner' USING ERRCODE = '23514'; END IF; RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.reject_last_project_owner() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF EXISTS (SELECT 1 FROM nanobot_collaboration.project_creator_edges WHERE organization_id = OLD.organization_id AND project_id = OLD.project_id) AND NOT EXISTS (SELECT 1 FROM nanobot_collaboration.project_role_edges WHERE organization_id = OLD.organization_id AND project_id = OLD.project_id AND role = 'owner') THEN RAISE EXCEPTION 'a project must retain an owner' USING ERRCODE = '23514'; END IF; RETURN NULL; END $$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.reject_private_owner_change() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$ BEGIN IF NEW.owner_user_id <> OLD.owner_user_id THEN RAISE EXCEPTION 'private resource owner is immutable' USING ERRCODE = '23514'; END IF; RETURN NEW; END $$;

DROP TRIGGER IF EXISTS collaboration_organization_creator_edge ON nanobot_collaboration.collaboration_organizations;
CREATE TRIGGER collaboration_organization_creator_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_organizations FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_organization_creator_edge();
DROP TRIGGER IF EXISTS collaboration_organization_owner_key_lock ON nanobot_collaboration.collaboration_organization_memberships;
CREATE TRIGGER collaboration_organization_owner_key_lock BEFORE INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_organization_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.lock_organization_owner_key();
DROP TRIGGER IF EXISTS collaboration_organization_role_edge ON nanobot_collaboration.collaboration_organization_memberships;
CREATE TRIGGER collaboration_organization_role_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_organization_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_organization_role_edge();
DROP TRIGGER IF EXISTS collaboration_project_creator_edge ON nanobot_collaboration.collaboration_projects;
CREATE TRIGGER collaboration_project_creator_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_projects FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_project_creator_edge();
DROP TRIGGER IF EXISTS collaboration_project_owner_key_lock ON nanobot_collaboration.collaboration_project_memberships;
CREATE TRIGGER collaboration_project_owner_key_lock BEFORE INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_project_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.lock_project_owner_key();
DROP TRIGGER IF EXISTS collaboration_project_role_edge ON nanobot_collaboration.collaboration_project_memberships;
CREATE TRIGGER collaboration_project_role_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_project_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_project_role_edge();
DROP TRIGGER IF EXISTS collaboration_vault_owner_edge ON nanobot_collaboration.collaboration_vaults;
CREATE TRIGGER collaboration_vault_owner_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_vaults FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_vault_owner_edge();
DROP TRIGGER IF EXISTS collaboration_vault_grant_edge ON nanobot_collaboration.collaboration_share_grants;
CREATE TRIGGER collaboration_vault_grant_edge AFTER INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_share_grants FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.sync_vault_grant_edge();
DROP TRIGGER IF EXISTS collaboration_persona_owner_immutable ON nanobot_collaboration.collaboration_personas;
CREATE TRIGGER collaboration_persona_owner_immutable BEFORE UPDATE ON nanobot_collaboration.collaboration_personas FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.reject_private_owner_change();
DROP TRIGGER IF EXISTS collaboration_personal_task_owner_immutable ON nanobot_collaboration.collaboration_personal_tasks;
CREATE TRIGGER collaboration_personal_task_owner_immutable BEFORE UPDATE ON nanobot_collaboration.collaboration_personal_tasks FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.reject_private_owner_change();
DROP TRIGGER IF EXISTS collaboration_last_organization_owner ON nanobot_collaboration.collaboration_organization_memberships;
CREATE CONSTRAINT TRIGGER collaboration_last_organization_owner AFTER UPDATE OR DELETE ON nanobot_collaboration.collaboration_organization_memberships DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.reject_last_organization_owner();
DROP TRIGGER IF EXISTS collaboration_last_project_owner ON nanobot_collaboration.collaboration_project_memberships;
CREATE CONSTRAINT TRIGGER collaboration_last_project_owner AFTER UPDATE OR DELETE ON nanobot_collaboration.collaboration_project_memberships DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.reject_last_project_owner();

-- Populate projections when a test database was interrupted after its base tables were loaded.
INSERT INTO nanobot_collaboration.organization_creator_edges SELECT id, created_by_user_id FROM nanobot_collaboration.collaboration_organizations ON CONFLICT (organization_id) DO UPDATE SET creator_user_id = EXCLUDED.creator_user_id;
INSERT INTO nanobot_collaboration.organization_role_edges SELECT organization_id, user_id, role FROM nanobot_collaboration.collaboration_organization_memberships ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role;
INSERT INTO nanobot_collaboration.project_creator_edges SELECT organization_id, id, created_by_user_id FROM nanobot_collaboration.collaboration_projects ON CONFLICT (organization_id, project_id) DO UPDATE SET creator_user_id = EXCLUDED.creator_user_id;
INSERT INTO nanobot_collaboration.project_role_edges SELECT organization_id, project_id, user_id, role FROM nanobot_collaboration.collaboration_project_memberships ON CONFLICT (organization_id, project_id, user_id) DO UPDATE SET role = EXCLUDED.role;
INSERT INTO nanobot_collaboration.vault_owner_edges SELECT organization_id, id, owner_user_id FROM nanobot_collaboration.collaboration_vaults ON CONFLICT (organization_id, vault_id) DO UPDATE SET owner_user_id = EXCLUDED.owner_user_id;
INSERT INTO nanobot_collaboration.vault_grant_edges SELECT id, organization_id, vault_id, grantee_user_id, resource_type, resource_id, permission, expires_at_ms, revoked_at_ms FROM nanobot_collaboration.collaboration_share_grants ON CONFLICT (grant_id) DO UPDATE SET organization_id = EXCLUDED.organization_id, vault_id = EXCLUDED.vault_id, grantee_user_id = EXCLUDED.grantee_user_id, resource_type = EXCLUDED.resource_type, resource_id = EXCLUDED.resource_id, permission = EXCLUDED.permission, expires_at_ms = EXCLUDED.expires_at_ms, revoked_at_ms = EXCLUDED.revoked_at_ms;

ALTER TABLE nanobot_collaboration.collaboration_users ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_users FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_organizations ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_organization_memberships ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_organization_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_identities ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_identities FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_vaults ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_vaults FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_personas ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_personas FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_share_grants ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_share_grants FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_projects ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_projects FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_project_memberships ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_project_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_task_lists ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_task_lists FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_project_tasks ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_project_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_personal_tasks ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_personal_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_extension_profiles ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_extension_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_context_sources ENABLE ROW LEVEL SECURITY; ALTER TABLE nanobot_collaboration.collaboration_context_sources FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS collaboration_users_select ON nanobot_collaboration.collaboration_users; CREATE POLICY collaboration_users_select ON nanobot_collaboration.collaboration_users FOR SELECT USING (id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_users_insert ON nanobot_collaboration.collaboration_users; CREATE POLICY collaboration_users_insert ON nanobot_collaboration.collaboration_users FOR INSERT WITH CHECK (id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_users_update ON nanobot_collaboration.collaboration_users; CREATE POLICY collaboration_users_update ON nanobot_collaboration.collaboration_users FOR UPDATE USING (id = nanobot_collaboration.nanobot_current_user_id()) WITH CHECK (id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_users_delete ON nanobot_collaboration.collaboration_users; CREATE POLICY collaboration_users_delete ON nanobot_collaboration.collaboration_users FOR DELETE USING (id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_organizations_select ON nanobot_collaboration.collaboration_organizations; CREATE POLICY collaboration_organizations_select ON nanobot_collaboration.collaboration_organizations FOR SELECT USING (nanobot_collaboration.nanobot_has_organization_role(id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_organizations_insert ON nanobot_collaboration.collaboration_organizations; CREATE POLICY collaboration_organizations_insert ON nanobot_collaboration.collaboration_organizations FOR INSERT WITH CHECK (created_by_user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_organizations_update ON nanobot_collaboration.collaboration_organizations; CREATE POLICY collaboration_organizations_update ON nanobot_collaboration.collaboration_organizations FOR UPDATE USING (nanobot_collaboration.nanobot_has_organization_role(id, ARRAY['owner','admin']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_organization_role(id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_organizations_delete ON nanobot_collaboration.collaboration_organizations; CREATE POLICY collaboration_organizations_delete ON nanobot_collaboration.collaboration_organizations FOR DELETE USING (nanobot_collaboration.nanobot_has_organization_role(id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_select ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_select ON nanobot_collaboration.collaboration_organization_memberships FOR SELECT USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_bootstrap ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_bootstrap ON nanobot_collaboration.collaboration_organization_memberships FOR INSERT WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id() AND role = 'owner' AND nanobot_collaboration.nanobot_is_organization_creator(organization_id) AND NOT nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_owner ON nanobot_collaboration.collaboration_organization_memberships;
DROP POLICY IF EXISTS collaboration_organization_memberships_owner_insert ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_owner_insert ON nanobot_collaboration.collaboration_organization_memberships FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_owner_update ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_owner_update ON nanobot_collaboration.collaboration_organization_memberships FOR UPDATE USING (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_owner_delete ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_owner_delete ON nanobot_collaboration.collaboration_organization_memberships FOR DELETE USING (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_admin_members ON nanobot_collaboration.collaboration_organization_memberships;
DROP POLICY IF EXISTS collaboration_organization_memberships_admin_member_insert ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_admin_member_insert ON nanobot_collaboration.collaboration_organization_memberships FOR INSERT WITH CHECK (role = 'member' AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_admin_member_update ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_admin_member_update ON nanobot_collaboration.collaboration_organization_memberships FOR UPDATE USING (role = 'member' AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[])) WITH CHECK (role = 'member' AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_organization_memberships_admin_member_delete ON nanobot_collaboration.collaboration_organization_memberships; CREATE POLICY collaboration_organization_memberships_admin_member_delete ON nanobot_collaboration.collaboration_organization_memberships FOR DELETE USING (role = 'member' AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_identities_lookup ON nanobot_collaboration.collaboration_identities; CREATE POLICY collaboration_identities_lookup ON nanobot_collaboration.collaboration_identities FOR SELECT USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR (channel = NULLIF(pg_catalog.current_setting('nanobot.identity_channel', true), '') AND sender_id = NULLIF(pg_catalog.current_setting('nanobot.identity_sender_id', true), '')));
DROP POLICY IF EXISTS collaboration_identities_insert ON nanobot_collaboration.collaboration_identities; CREATE POLICY collaboration_identities_insert ON nanobot_collaboration.collaboration_identities FOR INSERT WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_identities_update ON nanobot_collaboration.collaboration_identities; CREATE POLICY collaboration_identities_update ON nanobot_collaboration.collaboration_identities FOR UPDATE USING (user_id = nanobot_collaboration.nanobot_current_user_id()) WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_identities_delete ON nanobot_collaboration.collaboration_identities; CREATE POLICY collaboration_identities_delete ON nanobot_collaboration.collaboration_identities FOR DELETE USING (user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_vaults_select ON nanobot_collaboration.collaboration_vaults; CREATE POLICY collaboration_vaults_select ON nanobot_collaboration.collaboration_vaults FOR SELECT USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, id, 'vault', NULL, false));
DROP POLICY IF EXISTS collaboration_vaults_insert ON nanobot_collaboration.collaboration_vaults; CREATE POLICY collaboration_vaults_insert ON nanobot_collaboration.collaboration_vaults FOR INSERT WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_vaults_update ON nanobot_collaboration.collaboration_vaults; CREATE POLICY collaboration_vaults_update ON nanobot_collaboration.collaboration_vaults FOR UPDATE USING (nanobot_collaboration.nanobot_is_vault_owner(organization_id, id)) WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_vaults_delete ON nanobot_collaboration.collaboration_vaults; CREATE POLICY collaboration_vaults_delete ON nanobot_collaboration.collaboration_vaults FOR DELETE USING (nanobot_collaboration.nanobot_is_vault_owner(organization_id, id));
DROP POLICY IF EXISTS collaboration_personas_select ON nanobot_collaboration.collaboration_personas; CREATE POLICY collaboration_personas_select ON nanobot_collaboration.collaboration_personas FOR SELECT USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, default_vault_id, 'persona', id, false));
DROP POLICY IF EXISTS collaboration_personas_insert ON nanobot_collaboration.collaboration_personas; CREATE POLICY collaboration_personas_insert ON nanobot_collaboration.collaboration_personas FOR INSERT WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, default_vault_id, 'persona', id, true));
DROP POLICY IF EXISTS collaboration_personas_update ON nanobot_collaboration.collaboration_personas; CREATE POLICY collaboration_personas_update ON nanobot_collaboration.collaboration_personas FOR UPDATE USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, default_vault_id, 'persona', id, true)) WITH CHECK (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, default_vault_id, 'persona', id, true));
DROP POLICY IF EXISTS collaboration_personas_delete ON nanobot_collaboration.collaboration_personas; CREATE POLICY collaboration_personas_delete ON nanobot_collaboration.collaboration_personas FOR DELETE USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, default_vault_id, 'persona', id, true));
DROP POLICY IF EXISTS collaboration_share_grants_select ON nanobot_collaboration.collaboration_share_grants; CREATE POLICY collaboration_share_grants_select ON nanobot_collaboration.collaboration_share_grants FOR SELECT USING (grantee_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_is_vault_owner(organization_id, vault_id));
DROP POLICY IF EXISTS collaboration_share_grants_mutate ON nanobot_collaboration.collaboration_share_grants;
DROP POLICY IF EXISTS collaboration_share_grants_insert ON nanobot_collaboration.collaboration_share_grants; CREATE POLICY collaboration_share_grants_insert ON nanobot_collaboration.collaboration_share_grants FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_is_vault_owner(organization_id, vault_id));
DROP POLICY IF EXISTS collaboration_share_grants_update ON nanobot_collaboration.collaboration_share_grants; CREATE POLICY collaboration_share_grants_update ON nanobot_collaboration.collaboration_share_grants FOR UPDATE USING (nanobot_collaboration.nanobot_is_vault_owner(organization_id, vault_id)) WITH CHECK (nanobot_collaboration.nanobot_is_vault_owner(organization_id, vault_id));
DROP POLICY IF EXISTS collaboration_share_grants_delete ON nanobot_collaboration.collaboration_share_grants; CREATE POLICY collaboration_share_grants_delete ON nanobot_collaboration.collaboration_share_grants FOR DELETE USING (nanobot_collaboration.nanobot_is_vault_owner(organization_id, vault_id));
DROP POLICY IF EXISTS collaboration_projects_select ON nanobot_collaboration.collaboration_projects; CREATE POLICY collaboration_projects_select ON nanobot_collaboration.collaboration_projects FOR SELECT USING (nanobot_collaboration.nanobot_has_project_role(organization_id, id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_projects_insert ON nanobot_collaboration.collaboration_projects; CREATE POLICY collaboration_projects_insert ON nanobot_collaboration.collaboration_projects FOR INSERT WITH CHECK (created_by_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_projects_owner ON nanobot_collaboration.collaboration_projects; CREATE POLICY collaboration_projects_owner ON nanobot_collaboration.collaboration_projects FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, id, ARRAY['owner']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_projects_delete ON nanobot_collaboration.collaboration_projects; CREATE POLICY collaboration_projects_delete ON nanobot_collaboration.collaboration_projects FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_memberships_select ON nanobot_collaboration.collaboration_project_memberships; CREATE POLICY collaboration_project_memberships_select ON nanobot_collaboration.collaboration_project_memberships FOR SELECT USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_memberships_bootstrap ON nanobot_collaboration.collaboration_project_memberships; CREATE POLICY collaboration_project_memberships_bootstrap ON nanobot_collaboration.collaboration_project_memberships FOR INSERT WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id() AND role = 'owner' AND nanobot_collaboration.nanobot_is_project_creator(organization_id, project_id) AND NOT nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_memberships_owner ON nanobot_collaboration.collaboration_project_memberships;
DROP POLICY IF EXISTS collaboration_project_memberships_owner_insert ON nanobot_collaboration.collaboration_project_memberships; CREATE POLICY collaboration_project_memberships_owner_insert ON nanobot_collaboration.collaboration_project_memberships FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_memberships_owner_update ON nanobot_collaboration.collaboration_project_memberships; CREATE POLICY collaboration_project_memberships_owner_update ON nanobot_collaboration.collaboration_project_memberships FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_memberships_owner_delete ON nanobot_collaboration.collaboration_project_memberships; CREATE POLICY collaboration_project_memberships_owner_delete ON nanobot_collaboration.collaboration_project_memberships FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
"""

# Project aggregates share exact-project-member CRUD; the owner-only policies above
# cover project and project-membership destructive administration.
_RLS_DDL = r"""
DROP POLICY IF EXISTS collaboration_task_lists_member ON nanobot_collaboration.collaboration_task_lists;
DROP POLICY IF EXISTS collaboration_task_lists_select ON nanobot_collaboration.collaboration_task_lists; CREATE POLICY collaboration_task_lists_select ON nanobot_collaboration.collaboration_task_lists FOR SELECT USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_task_lists_insert ON nanobot_collaboration.collaboration_task_lists; CREATE POLICY collaboration_task_lists_insert ON nanobot_collaboration.collaboration_task_lists FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_task_lists_update ON nanobot_collaboration.collaboration_task_lists; CREATE POLICY collaboration_task_lists_update ON nanobot_collaboration.collaboration_task_lists FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_task_lists_delete ON nanobot_collaboration.collaboration_task_lists; CREATE POLICY collaboration_task_lists_delete ON nanobot_collaboration.collaboration_task_lists FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_tasks_member ON nanobot_collaboration.collaboration_project_tasks;
DROP POLICY IF EXISTS collaboration_project_tasks_select ON nanobot_collaboration.collaboration_project_tasks; CREATE POLICY collaboration_project_tasks_select ON nanobot_collaboration.collaboration_project_tasks FOR SELECT USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_tasks_insert ON nanobot_collaboration.collaboration_project_tasks; CREATE POLICY collaboration_project_tasks_insert ON nanobot_collaboration.collaboration_project_tasks FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_tasks_update ON nanobot_collaboration.collaboration_project_tasks; CREATE POLICY collaboration_project_tasks_update ON nanobot_collaboration.collaboration_project_tasks FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_project_tasks_delete ON nanobot_collaboration.collaboration_project_tasks; CREATE POLICY collaboration_project_tasks_delete ON nanobot_collaboration.collaboration_project_tasks FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_personal_tasks_select ON nanobot_collaboration.collaboration_personal_tasks; CREATE POLICY collaboration_personal_tasks_select ON nanobot_collaboration.collaboration_personal_tasks FOR SELECT USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, vault_id, 'personal_task', id, false));
DROP POLICY IF EXISTS collaboration_personal_tasks_insert ON nanobot_collaboration.collaboration_personal_tasks; CREATE POLICY collaboration_personal_tasks_insert ON nanobot_collaboration.collaboration_personal_tasks FOR INSERT WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, vault_id, 'personal_task', id, true));
DROP POLICY IF EXISTS collaboration_personal_tasks_mutate ON nanobot_collaboration.collaboration_personal_tasks; CREATE POLICY collaboration_personal_tasks_mutate ON nanobot_collaboration.collaboration_personal_tasks FOR UPDATE USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, vault_id, 'personal_task', id, true)) WITH CHECK (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, vault_id, 'personal_task', id, true));
DROP POLICY IF EXISTS collaboration_personal_tasks_delete ON nanobot_collaboration.collaboration_personal_tasks; CREATE POLICY collaboration_personal_tasks_delete ON nanobot_collaboration.collaboration_personal_tasks FOR DELETE USING (nanobot_collaboration.nanobot_can_access_vault_resource(organization_id, vault_id, 'personal_task', id, true));
DROP POLICY IF EXISTS collaboration_conversation_bindings_member ON nanobot_collaboration.collaboration_conversation_bindings;
DROP POLICY IF EXISTS collaboration_conversation_bindings_select ON nanobot_collaboration.collaboration_conversation_bindings; CREATE POLICY collaboration_conversation_bindings_select ON nanobot_collaboration.collaboration_conversation_bindings FOR SELECT USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_conversation_bindings_insert ON nanobot_collaboration.collaboration_conversation_bindings; CREATE POLICY collaboration_conversation_bindings_insert ON nanobot_collaboration.collaboration_conversation_bindings FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_conversation_bindings_update ON nanobot_collaboration.collaboration_conversation_bindings; CREATE POLICY collaboration_conversation_bindings_update ON nanobot_collaboration.collaboration_conversation_bindings FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_conversation_bindings_delete ON nanobot_collaboration.collaboration_conversation_bindings; CREATE POLICY collaboration_conversation_bindings_delete ON nanobot_collaboration.collaboration_conversation_bindings FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_extension_profiles_access ON nanobot_collaboration.collaboration_extension_profiles;
DROP POLICY IF EXISTS collaboration_extension_profiles_select ON nanobot_collaboration.collaboration_extension_profiles; CREATE POLICY collaboration_extension_profiles_select ON nanobot_collaboration.collaboration_extension_profiles FOR SELECT USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_extension_profiles_insert ON nanobot_collaboration.collaboration_extension_profiles; CREATE POLICY collaboration_extension_profiles_insert ON nanobot_collaboration.collaboration_extension_profiles FOR INSERT WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_extension_profiles_update ON nanobot_collaboration.collaboration_extension_profiles; CREATE POLICY collaboration_extension_profiles_update ON nanobot_collaboration.collaboration_extension_profiles FOR UPDATE USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[])) WITH CHECK (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_extension_profiles_delete ON nanobot_collaboration.collaboration_extension_profiles; CREATE POLICY collaboration_extension_profiles_delete ON nanobot_collaboration.collaboration_extension_profiles FOR DELETE USING (user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]));
DROP POLICY IF EXISTS collaboration_context_sources_member ON nanobot_collaboration.collaboration_context_sources;
DROP POLICY IF EXISTS collaboration_context_sources_select ON nanobot_collaboration.collaboration_context_sources; CREATE POLICY collaboration_context_sources_select ON nanobot_collaboration.collaboration_context_sources FOR SELECT USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_context_sources_insert ON nanobot_collaboration.collaboration_context_sources; CREATE POLICY collaboration_context_sources_insert ON nanobot_collaboration.collaboration_context_sources FOR INSERT WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_context_sources_update ON nanobot_collaboration.collaboration_context_sources; CREATE POLICY collaboration_context_sources_update ON nanobot_collaboration.collaboration_context_sources FOR UPDATE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[])) WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_context_sources_delete ON nanobot_collaboration.collaboration_context_sources; CREATE POLICY collaboration_context_sources_delete ON nanobot_collaboration.collaboration_context_sources FOR DELETE USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));

REVOKE ALL ON TABLE nanobot_collaboration.organization_creator_edges, nanobot_collaboration.organization_role_edges, nanobot_collaboration.project_creator_edges, nanobot_collaboration.project_role_edges, nanobot_collaboration.vault_owner_edges, nanobot_collaboration.vault_grant_edges FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA nanobot_collaboration FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA nanobot_collaboration TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.organization_creator_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.organization_role_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.project_creator_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.project_role_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.vault_owner_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.vault_grant_edges OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_current_user_id() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_has_organization_role(varchar, varchar[]) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_has_project_role(varchar, varchar, varchar[]) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_is_organization_creator(varchar) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_is_project_creator(varchar, varchar) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_is_vault_owner(varchar, varchar) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.nanobot_can_access_vault_resource(varchar, varchar, varchar, varchar, boolean) OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.lock_organization_owner_key() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.lock_project_owner_key() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_organization_creator_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_organization_role_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_project_creator_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_project_role_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_vault_owner_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.sync_vault_grant_edge() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.reject_last_organization_owner() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.reject_last_project_owner() OWNER TO nanobot_collaboration_policy_owner;
ALTER FUNCTION nanobot_collaboration.reject_private_owner_change() OWNER TO nanobot_collaboration_policy_owner;
REVOKE CREATE ON SCHEMA nanobot_collaboration FROM nanobot_collaboration_policy_owner;
"""

INITIAL_SCHEMA_DDL = _BASE_SCHEMA_DDL + _RLS_DDL

MIGRATION_2_DDL = r"""
ALTER TABLE nanobot_collaboration.collaboration_organizations
    ADD COLUMN IF NOT EXISTS is_personal boolean NOT NULL DEFAULT false;
ALTER TABLE nanobot_collaboration.collaboration_organizations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_vaults NO FORCE ROW LEVEL SECURITY;
UPDATE nanobot_collaboration.collaboration_organizations AS organization
SET is_personal = true
FROM nanobot_collaboration.collaboration_users AS user_row
JOIN nanobot_collaboration.collaboration_vaults AS vault
    ON vault.id = user_row.default_vault_id
WHERE organization.created_by_user_id = user_row.id
  AND vault.organization_id = organization.id;
ALTER TABLE nanobot_collaboration.collaboration_organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_users FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_vaults FORCE ROW LEVEL SECURITY;

ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings NO FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM nanobot_collaboration.collaboration_conversation_bindings
        GROUP BY channel, conversation_id, thread_id
        HAVING pg_catalog.count(*) > 1
    ) THEN
        RAISE EXCEPTION 'cannot promote conversation binding key: duplicate external binding exists'
            USING ERRCODE = '23505';
    END IF;
END
$$;
DO $$
DECLARE
    old_constraint name;
BEGIN
    FOR old_constraint IN
        SELECT constraint_row.conname
        FROM pg_catalog.pg_constraint AS constraint_row
        WHERE constraint_row.conrelid = 'nanobot_collaboration.collaboration_conversation_bindings'::pg_catalog.regclass
          AND constraint_row.contype = 'u'
          AND constraint_row.conkey = ARRAY[
              (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'organization_id' AND NOT attribute_row.attisdropped),
              (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'channel' AND NOT attribute_row.attisdropped),
              (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'conversation_id' AND NOT attribute_row.attisdropped),
              (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'thread_id' AND NOT attribute_row.attisdropped)
          ]::smallint[]
    LOOP
        EXECUTE pg_catalog.format('ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings DROP CONSTRAINT %I', old_constraint);
    END LOOP;
END
$$;
DO $$
DECLARE
    global_constraint name;
BEGIN
    SELECT constraint_row.conname
    INTO global_constraint
    FROM pg_catalog.pg_constraint AS constraint_row
    WHERE constraint_row.conrelid = 'nanobot_collaboration.collaboration_conversation_bindings'::pg_catalog.regclass
      AND constraint_row.contype = 'u'
      AND constraint_row.conkey = ARRAY[
          (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'channel' AND NOT attribute_row.attisdropped),
          (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'conversation_id' AND NOT attribute_row.attisdropped),
          (SELECT attribute_row.attnum FROM pg_catalog.pg_attribute AS attribute_row WHERE attribute_row.attrelid = constraint_row.conrelid AND attribute_row.attname = 'thread_id' AND NOT attribute_row.attisdropped)
      ]::smallint[];
    IF global_constraint IS NULL THEN
        ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings
            ADD CONSTRAINT collaboration_conversation_bindings_global_external_key
            UNIQUE (channel, conversation_id, thread_id);
    ELSIF global_constraint <> 'collaboration_conversation_bindings_global_external_key' THEN
        EXECUTE pg_catalog.format(
            'ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings RENAME CONSTRAINT %I TO collaboration_conversation_bindings_global_external_key',
            global_constraint
        );
    END IF;
END
$$;
ALTER TABLE nanobot_collaboration.collaboration_conversation_bindings FORCE ROW LEVEL SECURITY;

GRANT USAGE, CREATE ON SCHEMA nanobot_collaboration TO nanobot_collaboration_policy_owner;
SET LOCAL ROLE nanobot_collaboration_policy_owner;
CREATE OR REPLACE FUNCTION nanobot_collaboration.lock_organization_owner_key() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    old_lock bigint;
    new_lock bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(NEW.organization_id, 0));
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(OLD.organization_id, 0));
        RETURN OLD;
    END IF;
    old_lock := pg_catalog.hashtextextended(OLD.organization_id, 0);
    new_lock := pg_catalog.hashtextextended(NEW.organization_id, 0);
    IF old_lock <= new_lock THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
        IF old_lock <> new_lock THEN PERFORM pg_catalog.pg_advisory_xact_lock(new_lock); END IF;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(new_lock);
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
    END IF;
    RETURN NEW;
END
$$;
CREATE OR REPLACE FUNCTION nanobot_collaboration.lock_project_owner_key() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
    old_lock bigint;
    new_lock bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(NEW.organization_id || ':' || NEW.project_id, 0));
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(OLD.organization_id || ':' || OLD.project_id, 0));
        RETURN OLD;
    END IF;
    old_lock := pg_catalog.hashtextextended(OLD.organization_id || ':' || OLD.project_id, 0);
    new_lock := pg_catalog.hashtextextended(NEW.organization_id || ':' || NEW.project_id, 0);
    IF old_lock <= new_lock THEN
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
        IF old_lock <> new_lock THEN PERFORM pg_catalog.pg_advisory_xact_lock(new_lock); END IF;
    ELSE
        PERFORM pg_catalog.pg_advisory_xact_lock(new_lock);
        PERFORM pg_catalog.pg_advisory_xact_lock(old_lock);
    END IF;
    RETURN NEW;
END
$$;
RESET ROLE;
REVOKE CREATE ON SCHEMA nanobot_collaboration FROM nanobot_collaboration_policy_owner;
DROP TRIGGER IF EXISTS collaboration_organization_owner_key_lock ON nanobot_collaboration.collaboration_organization_memberships;
CREATE TRIGGER collaboration_organization_owner_key_lock BEFORE INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_organization_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.lock_organization_owner_key();
DROP TRIGGER IF EXISTS collaboration_project_owner_key_lock ON nanobot_collaboration.collaboration_project_memberships;
CREATE TRIGGER collaboration_project_owner_key_lock BEFORE INSERT OR UPDATE OR DELETE ON nanobot_collaboration.collaboration_project_memberships FOR EACH ROW EXECUTE FUNCTION nanobot_collaboration.lock_project_owner_key();
SET LOCAL ROLE nanobot_collaboration_policy_owner;
REVOKE ALL ON FUNCTION nanobot_collaboration.lock_organization_owner_key(), nanobot_collaboration.lock_project_owner_key() FROM PUBLIC;
RESET ROLE;
"""

MIGRATION_3_DDL = r"""
ALTER TABLE nanobot_collaboration.collaboration_users
    ADD COLUMN IF NOT EXISTS default_organization_id varchar(128),
    ADD COLUMN IF NOT EXISTS default_bot_id varchar(128);

CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_bots (
    id varchar(128) PRIMARY KEY,
    organization_id varchar(128) NOT NULL,
    owner_user_id varchar(128) NOT NULL,
    name varchar(256) NOT NULL CHECK (pg_catalog.length(pg_catalog.btrim(name)) > 0),
    avatar_url varchar(4096),
    persona_id varchar(128) REFERENCES nanobot_collaboration.collaboration_personas(id) ON DELETE SET NULL,
    state varchar(16) NOT NULL CHECK (state IN ('active', 'disabled')),
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= created_at_ms),
    UNIQUE (organization_id, id),
    FOREIGN KEY (organization_id, owner_user_id)
        REFERENCES nanobot_collaboration.collaboration_organization_memberships
        (organization_id, user_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_bot_project_assignments (
    organization_id varchar(128) NOT NULL,
    bot_id varchar(128) NOT NULL,
    project_id varchar(128) NOT NULL,
    assigned_by_user_id varchar(128) NOT NULL,
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (organization_id, bot_id, project_id),
    FOREIGN KEY (organization_id, bot_id)
        REFERENCES nanobot_collaboration.collaboration_bots (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, project_id)
        REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, assigned_by_user_id)
        REFERENCES nanobot_collaboration.collaboration_organization_memberships
        (organization_id, user_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_bot_channel_assignments (
    organization_id varchar(128) NOT NULL,
    bot_id varchar(128) NOT NULL,
    channel_type varchar(128) NOT NULL CHECK (pg_catalog.length(pg_catalog.btrim(channel_type)) > 0),
    instance_id varchar(128) NOT NULL CHECK (pg_catalog.length(pg_catalog.btrim(instance_id)) > 0),
    claimed_by_user_id varchar(128) NOT NULL,
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (channel_type, instance_id),
    UNIQUE (organization_id, bot_id, channel_type, instance_id),
    FOREIGN KEY (organization_id, bot_id)
        REFERENCES nanobot_collaboration.collaboration_bots (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, claimed_by_user_id)
        REFERENCES nanobot_collaboration.collaboration_organization_memberships
        (organization_id, user_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_bot_project_channels (
    organization_id varchar(128) NOT NULL,
    bot_id varchar(128) NOT NULL,
    project_id varchar(128) NOT NULL,
    channel_type varchar(128) NOT NULL,
    instance_id varchar(128) NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= 0),
    PRIMARY KEY (organization_id, bot_id, project_id, channel_type, instance_id),
    FOREIGN KEY (organization_id, bot_id, project_id)
        REFERENCES nanobot_collaboration.collaboration_bot_project_assignments
        (organization_id, bot_id, project_id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, bot_id, channel_type, instance_id)
        REFERENCES nanobot_collaboration.collaboration_bot_channel_assignments
        (organization_id, bot_id, channel_type, instance_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_bot_capability_profiles (
    organization_id varchar(128) NOT NULL,
    bot_id varchar(128) NOT NULL,
    project_id varchar(128),
    scope_project_id varchar(128) NOT NULL,
    revision integer NOT NULL CHECK (revision >= 0),
    settings jsonb NOT NULL CHECK (pg_catalog.octet_length(settings::text) <= 65536),
    updated_at_ms bigint NOT NULL CHECK (updated_at_ms >= 0),
    PRIMARY KEY (organization_id, bot_id, scope_project_id),
    CHECK ((project_id IS NULL AND scope_project_id = '') OR project_id = scope_project_id),
    FOREIGN KEY (organization_id, bot_id)
        REFERENCES nanobot_collaboration.collaboration_bots (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, project_id)
        REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_pairing_challenges (
    id varchar(128) PRIMARY KEY,
    code_digest char(64) NOT NULL UNIQUE,
    requested_by_user_id varchar(128) NOT NULL,
    purpose varchar(32) NOT NULL CHECK (purpose IN ('claim_channel', 'assign_bot_project')),
    organization_id varchar(128) NOT NULL,
    bot_id varchar(128) NOT NULL,
    project_id varchar(128),
    channel_type varchar(128) NOT NULL,
    instance_id varchar(128) NOT NULL,
    channel_revision varchar(256) NOT NULL DEFAULT 'legacy-unbound',
    expires_at_ms bigint NOT NULL CHECK (expires_at_ms >= 0),
    verified_at_ms bigint,
    verified_sender_id varchar(512),
    consumed_at_ms bigint,
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    CHECK (expires_at_ms >= created_at_ms),
    CHECK ((verified_at_ms IS NULL) = (verified_sender_id IS NULL)),
    FOREIGN KEY (organization_id, bot_id)
        REFERENCES nanobot_collaboration.collaboration_bots (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, project_id)
        REFERENCES nanobot_collaboration.collaboration_projects (organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, requested_by_user_id)
        REFERENCES nanobot_collaboration.collaboration_organization_memberships
        (organization_id, user_id) ON DELETE CASCADE
);

ALTER TABLE nanobot_collaboration.collaboration_users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_organizations NO FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_projects NO FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_extension_profiles NO FORCE ROW LEVEL SECURITY;
INSERT INTO nanobot_collaboration.collaboration_bots (
    id, organization_id, owner_user_id, name, avatar_url, persona_id, state,
    created_at_ms, updated_at_ms
)
SELECT 'bot-' || pg_catalog.substr(pg_catalog.md5(user_row.id), 1, 24),
       organization.id, user_row.id, 'Personal bot', NULL, user_row.default_persona_id,
       'active', user_row.created_at_ms, user_row.updated_at_ms
FROM nanobot_collaboration.collaboration_users AS user_row
JOIN nanobot_collaboration.collaboration_organizations AS organization
  ON organization.created_by_user_id = user_row.id AND organization.is_personal
ON CONFLICT (id) DO NOTHING;
UPDATE nanobot_collaboration.collaboration_users AS user_row
SET default_organization_id = COALESCE(
        (SELECT project.organization_id
         FROM nanobot_collaboration.collaboration_projects AS project
         JOIN nanobot_collaboration.collaboration_project_memberships AS membership
           ON membership.organization_id = project.organization_id
          AND membership.project_id = project.id
          AND membership.user_id = user_row.id
         WHERE project.id = user_row.default_project_id),
        organization.id
    ),
    default_bot_id = CASE
        WHEN COALESCE(
            (SELECT project.organization_id
             FROM nanobot_collaboration.collaboration_projects AS project
             JOIN nanobot_collaboration.collaboration_project_memberships AS membership
               ON membership.organization_id = project.organization_id
              AND membership.project_id = project.id
              AND membership.user_id = user_row.id
             WHERE project.id = user_row.default_project_id),
            organization.id
        ) = organization.id THEN bot.id
        ELSE NULL
    END
FROM nanobot_collaboration.collaboration_organizations AS organization
JOIN nanobot_collaboration.collaboration_bots AS bot
  ON bot.organization_id = organization.id
 AND bot.owner_user_id = organization.created_by_user_id
WHERE organization.created_by_user_id = user_row.id
  AND organization.is_personal;
INSERT INTO nanobot_collaboration.collaboration_bot_project_assignments (
    organization_id, bot_id, project_id, assigned_by_user_id, created_at_ms
)
SELECT project.organization_id, bot.id, project.id, user_row.id,
       user_row.created_at_ms
FROM nanobot_collaboration.collaboration_users AS user_row
JOIN nanobot_collaboration.collaboration_projects AS project
  ON project.id = user_row.default_project_id
JOIN nanobot_collaboration.collaboration_bots AS bot
  ON bot.id = user_row.default_bot_id
 AND bot.organization_id = project.organization_id
ON CONFLICT DO NOTHING;
INSERT INTO nanobot_collaboration.collaboration_bot_capability_profiles (
    organization_id, bot_id, project_id, scope_project_id, revision, settings, updated_at_ms
)
SELECT project.organization_id, bot.id, profile.project_id,
       profile.project_id, profile.revision, profile.settings, profile.updated_at_ms
FROM nanobot_collaboration.collaboration_extension_profiles AS profile
JOIN nanobot_collaboration.collaboration_users AS user_row
  ON user_row.id = profile.user_id
 AND user_row.default_bot_id IS NOT NULL
 AND user_row.default_project_id = profile.project_id
JOIN nanobot_collaboration.collaboration_projects AS project
  ON project.id = profile.project_id
JOIN nanobot_collaboration.collaboration_bots AS bot
  ON bot.id = user_row.default_bot_id
 AND bot.organization_id = project.organization_id
ON CONFLICT DO NOTHING;
ALTER TABLE nanobot_collaboration.collaboration_users FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_organizations FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_projects FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_extension_profiles FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'collaboration_users_default_organization_fk'
          AND conrelid = 'nanobot_collaboration.collaboration_users'::pg_catalog.regclass
    ) THEN
        ALTER TABLE nanobot_collaboration.collaboration_users
            ADD CONSTRAINT collaboration_users_default_organization_fk
            FOREIGN KEY (default_organization_id)
            REFERENCES nanobot_collaboration.collaboration_organizations(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'collaboration_users_default_bot_fk'
          AND conrelid = 'nanobot_collaboration.collaboration_users'::pg_catalog.regclass
    ) THEN
        ALTER TABLE nanobot_collaboration.collaboration_users
            ADD CONSTRAINT collaboration_users_default_bot_fk
            FOREIGN KEY (default_bot_id)
            REFERENCES nanobot_collaboration.collaboration_bots(id) ON DELETE SET NULL;
    END IF;
END
$$;

ALTER TABLE nanobot_collaboration.collaboration_bots ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bots FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_channel_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_channel_assignments FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_channels FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_capability_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_bot_capability_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_pairing_challenges ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_pairing_challenges FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS collaboration_bots_select ON nanobot_collaboration.collaboration_bots;
CREATE POLICY collaboration_bots_select ON nanobot_collaboration.collaboration_bots FOR SELECT
USING (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_bots_insert ON nanobot_collaboration.collaboration_bots;
CREATE POLICY collaboration_bots_insert ON nanobot_collaboration.collaboration_bots FOR INSERT
WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_bots_update ON nanobot_collaboration.collaboration_bots;
CREATE POLICY collaboration_bots_update ON nanobot_collaboration.collaboration_bots FOR UPDATE
USING (owner_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]))
WITH CHECK (owner_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));
DROP POLICY IF EXISTS collaboration_bots_delete ON nanobot_collaboration.collaboration_bots;
CREATE POLICY collaboration_bots_delete ON nanobot_collaboration.collaboration_bots FOR DELETE
USING (owner_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));

DROP POLICY IF EXISTS collaboration_bot_projects_select ON nanobot_collaboration.collaboration_bot_project_assignments;
CREATE POLICY collaboration_bot_projects_select ON nanobot_collaboration.collaboration_bot_project_assignments FOR SELECT
USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_bot_projects_write ON nanobot_collaboration.collaboration_bot_project_assignments;
CREATE POLICY collaboration_bot_projects_write ON nanobot_collaboration.collaboration_bot_project_assignments FOR ALL
USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]) AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]))
WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]) AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));

DROP POLICY IF EXISTS collaboration_bot_channels_select ON nanobot_collaboration.collaboration_bot_channel_assignments;
CREATE POLICY collaboration_bot_channels_select ON nanobot_collaboration.collaboration_bot_channel_assignments FOR SELECT
USING (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_bot_channels_write ON nanobot_collaboration.collaboration_bot_channel_assignments;
CREATE POLICY collaboration_bot_channels_write ON nanobot_collaboration.collaboration_bot_channel_assignments FOR ALL
USING (claimed_by_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]))
WITH CHECK (claimed_by_user_id = nanobot_collaboration.nanobot_current_user_id() OR nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));

DROP POLICY IF EXISTS collaboration_bot_project_channels_select ON nanobot_collaboration.collaboration_bot_project_channels;
CREATE POLICY collaboration_bot_project_channels_select ON nanobot_collaboration.collaboration_bot_project_channels FOR SELECT
USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_bot_project_channels_write ON nanobot_collaboration.collaboration_bot_project_channels;
CREATE POLICY collaboration_bot_project_channels_write ON nanobot_collaboration.collaboration_bot_project_channels FOR ALL
USING (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]) AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]))
WITH CHECK (nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[]) AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]));

DROP POLICY IF EXISTS collaboration_bot_profiles_select ON nanobot_collaboration.collaboration_bot_capability_profiles;
CREATE POLICY collaboration_bot_profiles_select ON nanobot_collaboration.collaboration_bot_capability_profiles FOR SELECT
USING (project_id IS NULL AND nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin','member']::varchar[]) OR project_id IS NOT NULL AND nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner','member']::varchar[]));
DROP POLICY IF EXISTS collaboration_bot_profiles_write ON nanobot_collaboration.collaboration_bot_capability_profiles;
CREATE POLICY collaboration_bot_profiles_write ON nanobot_collaboration.collaboration_bot_capability_profiles FOR ALL
USING (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]) AND (project_id IS NULL OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[])))
WITH CHECK (nanobot_collaboration.nanobot_has_organization_role(organization_id, ARRAY['owner','admin']::varchar[]) AND (project_id IS NULL OR nanobot_collaboration.nanobot_has_project_role(organization_id, project_id, ARRAY['owner']::varchar[])));

DROP POLICY IF EXISTS collaboration_pairing_select ON nanobot_collaboration.collaboration_pairing_challenges;
CREATE POLICY collaboration_pairing_select ON nanobot_collaboration.collaboration_pairing_challenges FOR SELECT
USING (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id() OR (nanobot_collaboration.nanobot_current_user_id() IS NULL AND consumed_at_ms IS NULL AND expires_at_ms >= pg_catalog.floor(pg_catalog.date_part('epoch', pg_catalog.clock_timestamp()) * 1000)::bigint AND (CASE WHEN instance_id = 'default' THEN channel_type ELSE channel_type || '.' || instance_id END) = NULLIF(pg_catalog.current_setting('nanobot.identity_channel', true), '')));
DROP POLICY IF EXISTS collaboration_pairing_insert ON nanobot_collaboration.collaboration_pairing_challenges;
CREATE POLICY collaboration_pairing_insert ON nanobot_collaboration.collaboration_pairing_challenges FOR INSERT
WITH CHECK (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_pairing_update ON nanobot_collaboration.collaboration_pairing_challenges;
CREATE POLICY collaboration_pairing_update ON nanobot_collaboration.collaboration_pairing_challenges FOR UPDATE
USING (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id())
WITH CHECK (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_pairing_delete ON nanobot_collaboration.collaboration_pairing_challenges;
CREATE POLICY collaboration_pairing_delete ON nanobot_collaboration.collaboration_pairing_challenges FOR DELETE
USING (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id());

GRANT USAGE, CREATE ON SCHEMA nanobot_collaboration TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_bots OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_assignments OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_bot_channel_assignments OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_bot_project_channels OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_bot_capability_profiles OWNER TO nanobot_collaboration_policy_owner;
ALTER TABLE nanobot_collaboration.collaboration_pairing_challenges OWNER TO nanobot_collaboration_policy_owner;
REVOKE CREATE ON SCHEMA nanobot_collaboration FROM nanobot_collaboration_policy_owner;
"""

MIGRATION_4_DDL = r"""
SET LOCAL ROLE nanobot_collaboration_policy_owner;
DROP POLICY IF EXISTS collaboration_pairing_select ON nanobot_collaboration.collaboration_pairing_challenges;
CREATE POLICY collaboration_pairing_select ON nanobot_collaboration.collaboration_pairing_challenges FOR SELECT
USING (requested_by_user_id = nanobot_collaboration.nanobot_current_user_id() OR (nanobot_collaboration.nanobot_current_user_id() IS NULL AND consumed_at_ms IS NULL AND expires_at_ms >= pg_catalog.floor(pg_catalog.date_part('epoch', pg_catalog.clock_timestamp()) * 1000)::bigint AND (CASE WHEN instance_id = 'default' THEN channel_type ELSE channel_type || '.' || instance_id END) = NULLIF(pg_catalog.current_setting('nanobot.identity_channel', true), '')));
RESET ROLE;
"""

MIGRATION_5_DDL = r"""
GRANT USAGE, CREATE ON SCHEMA nanobot_collaboration
    TO nanobot_collaboration_policy_owner;
SET LOCAL ROLE nanobot_collaboration_policy_owner;
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_channel_claim_registry (
    channel_type varchar(128) NOT NULL,
    instance_id varchar(128) NOT NULL,
    PRIMARY KEY (channel_type, instance_id),
    CHECK (pg_catalog.length(pg_catalog.btrim(channel_type)) > 0),
    CHECK (pg_catalog.length(pg_catalog.btrim(instance_id)) > 0)
);
REVOKE ALL ON TABLE nanobot_collaboration.collaboration_channel_claim_registry FROM PUBLIC;
ALTER TABLE nanobot_collaboration.collaboration_channel_claim_registry
    OWNER TO nanobot_collaboration_policy_owner;

CREATE OR REPLACE FUNCTION nanobot_collaboration.nanobot_sync_channel_claim_registry()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, nanobot_collaboration
AS $$
BEGIN
    IF TG_OP = 'DELETE' OR (
        TG_OP = 'UPDATE'
        AND (OLD.channel_type, OLD.instance_id) IS DISTINCT FROM
            (NEW.channel_type, NEW.instance_id)
    ) THEN
        DELETE FROM nanobot_collaboration.collaboration_channel_claim_registry
        WHERE channel_type = OLD.channel_type AND instance_id = OLD.instance_id;
    END IF;
    IF TG_OP <> 'DELETE' THEN
        INSERT INTO nanobot_collaboration.collaboration_channel_claim_registry
            (channel_type, instance_id)
        VALUES (NEW.channel_type, NEW.instance_id)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN COALESCE(NEW, OLD);
END
$$;
ALTER TABLE nanobot_collaboration.collaboration_bot_channel_assignments
    NO FORCE ROW LEVEL SECURITY;
ALTER FUNCTION nanobot_collaboration.nanobot_sync_channel_claim_registry()
    OWNER TO nanobot_collaboration_policy_owner;

SET LOCAL row_security = off;
INSERT INTO nanobot_collaboration.collaboration_channel_claim_registry
    (channel_type, instance_id)
SELECT channel_type, instance_id
FROM nanobot_collaboration.collaboration_bot_channel_assignments
ON CONFLICT DO NOTHING;

DROP TRIGGER IF EXISTS collaboration_sync_channel_claim_registry
    ON nanobot_collaboration.collaboration_bot_channel_assignments;
CREATE TRIGGER collaboration_sync_channel_claim_registry
AFTER INSERT OR UPDATE OF channel_type, instance_id OR DELETE
ON nanobot_collaboration.collaboration_bot_channel_assignments
FOR EACH ROW EXECUTE FUNCTION
    nanobot_collaboration.nanobot_sync_channel_claim_registry();
ALTER TABLE nanobot_collaboration.collaboration_bot_channel_assignments
    FORCE ROW LEVEL SECURITY;
RESET ROLE;
SET LOCAL row_security = on;
REVOKE CREATE ON SCHEMA nanobot_collaboration
    FROM nanobot_collaboration_policy_owner;
"""

MIGRATION_6_DDL = r"""
ALTER TABLE nanobot_collaboration.collaboration_pairing_challenges
    ADD COLUMN IF NOT EXISTS channel_revision varchar(256) NOT NULL DEFAULT 'legacy-unbound';
"""

# Channel provenance recorded at self-service provisioning time.  An instance with no row
# here predates the record and is deliberately invisible to every member: guessing a
# creator would turn a missing attribution into a takeover path.
MIGRATION_7_DDL = r"""
GRANT USAGE, CREATE ON SCHEMA nanobot_collaboration
    TO nanobot_collaboration_policy_owner;
CREATE TABLE IF NOT EXISTS nanobot_collaboration.collaboration_channel_provisions (
    channel_type varchar(128) NOT NULL
        CHECK (pg_catalog.length(pg_catalog.btrim(channel_type)) > 0),
    instance_id varchar(128) NOT NULL
        CHECK (pg_catalog.length(pg_catalog.btrim(instance_id)) > 0),
    organization_id varchar(128) NOT NULL,
    created_by_user_id varchar(128) NOT NULL,
    created_at_ms bigint NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (channel_type, instance_id),
    FOREIGN KEY (organization_id, created_by_user_id)
        REFERENCES nanobot_collaboration.collaboration_organization_memberships
        (organization_id, user_id) ON DELETE CASCADE
);
ALTER TABLE nanobot_collaboration.collaboration_channel_provisions
    ENABLE ROW LEVEL SECURITY;
ALTER TABLE nanobot_collaboration.collaboration_channel_provisions
    FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS collaboration_channel_provisions_select
    ON nanobot_collaboration.collaboration_channel_provisions;
CREATE POLICY collaboration_channel_provisions_select
ON nanobot_collaboration.collaboration_channel_provisions FOR SELECT
USING (created_by_user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_channel_provisions_insert
    ON nanobot_collaboration.collaboration_channel_provisions;
CREATE POLICY collaboration_channel_provisions_insert
ON nanobot_collaboration.collaboration_channel_provisions FOR INSERT
WITH CHECK (
    created_by_user_id = nanobot_collaboration.nanobot_current_user_id()
    AND nanobot_collaboration.nanobot_has_organization_role(
        organization_id, ARRAY['owner','admin','member']::varchar[]
    )
);
DROP POLICY IF EXISTS collaboration_channel_provisions_update
    ON nanobot_collaboration.collaboration_channel_provisions;
CREATE POLICY collaboration_channel_provisions_update
ON nanobot_collaboration.collaboration_channel_provisions FOR UPDATE
USING (created_by_user_id = nanobot_collaboration.nanobot_current_user_id())
WITH CHECK (created_by_user_id = nanobot_collaboration.nanobot_current_user_id());
DROP POLICY IF EXISTS collaboration_channel_provisions_delete
    ON nanobot_collaboration.collaboration_channel_provisions;
CREATE POLICY collaboration_channel_provisions_delete
ON nanobot_collaboration.collaboration_channel_provisions FOR DELETE
USING (created_by_user_id = nanobot_collaboration.nanobot_current_user_id());

ALTER TABLE nanobot_collaboration.collaboration_channel_provisions
    OWNER TO nanobot_collaboration_policy_owner;
REVOKE CREATE ON SCHEMA nanobot_collaboration
    FROM nanobot_collaboration_policy_owner;
"""


MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, INITIAL_SCHEMA_DDL),
    (2, MIGRATION_2_DDL),
    (3, MIGRATION_3_DDL),
    (4, MIGRATION_4_DDL),
    (5, MIGRATION_5_DDL),
    (6, MIGRATION_6_DDL),
    (7, MIGRATION_7_DDL),
)
