--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13
-- Dumped by pg_dump version 16.13

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: auth_method; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.auth_method AS ENUM (
    'local',
    'oidc:google',
    'oidc:azure',
    'oidc:okta',
    'oidc:github',
    'oidc:generic'
);


--
-- Name: log_level; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.log_level AS ENUM (
    'debug',
    'info',
    'warning',
    'error'
);


--
-- Name: pipeline_mode; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.pipeline_mode AS ENUM (
    'batch',
    'stream'
);


--
-- Name: run_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.run_status AS ENUM (
    'pending',
    'running',
    'succeeded',
    'failed',
    'cancelled'
);


--
-- Name: workspace_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.workspace_role AS ENUM (
    'owner',
    'editor',
    'runner',
    'viewer'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid NOT NULL,
    actor_user_id uuid,
    workspace_id uuid,
    action character varying(128) NOT NULL,
    resource_type character varying(64) NOT NULL,
    resource_id character varying(64),
    before_json jsonb,
    after_json jsonb,
    ip character varying(64),
    user_agent text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: connections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connections (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    type character varying(64) NOT NULL,
    name character varying(255) NOT NULL,
    config_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    secret_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cursors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cursors (
    workspace_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    cursor_column character varying(255) NOT NULL,
    cursor_value jsonb,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: memberships; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memberships (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role public.workspace_role NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: personal_access_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.personal_access_tokens (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    prefix character varying(32) NOT NULL,
    token_hash character varying(255) NOT NULL,
    expires_at timestamp with time zone,
    last_used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_versions (
    id uuid NOT NULL,
    pipeline_id uuid NOT NULL,
    version integer NOT NULL,
    config_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_current boolean DEFAULT false NOT NULL,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipelines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipelines (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: run_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.run_logs (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    ts timestamp with time zone DEFAULT now() NOT NULL,
    level public.log_level DEFAULT 'info'::public.log_level NOT NULL,
    message text NOT NULL,
    context_json jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: run_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.run_metrics (
    id uuid NOT NULL,
    run_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    value double precision NOT NULL,
    attrs_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.runs (
    id uuid NOT NULL,
    workspace_id uuid NOT NULL,
    pipeline_id uuid NOT NULL,
    pipeline_version_id uuid NOT NULL,
    schedule_id uuid,
    triggered_by_user_id uuid,
    status public.run_status DEFAULT 'pending'::public.run_status NOT NULL,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    heartbeat_at timestamp with time zone,
    worker_id character varying(255),
    records_read integer DEFAULT 0 NOT NULL,
    records_written integer DEFAULT 0 NOT NULL,
    duration_seconds double precision,
    error_class character varying(255),
    error_message text,
    result_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedules (
    id uuid NOT NULL,
    pipeline_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    cron_expr character varying(64),
    mode public.pipeline_mode NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    config_overrides jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by_user_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    name character varying(255) NOT NULL,
    auth_method public.auth_method DEFAULT 'local'::public.auth_method NOT NULL,
    password_hash character varying(255),
    is_superadmin boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: workspaces; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.workspaces (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    slug character varying(64) NOT NULL,
    color_hex character varying(9) DEFAULT '#FF3D8B'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: audit_log audit_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_pkey PRIMARY KEY (id);


--
-- Name: connections connections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connections
    ADD CONSTRAINT connections_pkey PRIMARY KEY (id);


--
-- Name: cursors cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cursors
    ADD CONSTRAINT cursors_pkey PRIMARY KEY (workspace_id, name);


--
-- Name: memberships memberships_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_pkey PRIMARY KEY (id);


--
-- Name: personal_access_tokens personal_access_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_access_tokens
    ADD CONSTRAINT personal_access_tokens_pkey PRIMARY KEY (id);


--
-- Name: personal_access_tokens personal_access_tokens_prefix_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_access_tokens
    ADD CONSTRAINT personal_access_tokens_prefix_key UNIQUE (prefix);


--
-- Name: pipeline_versions pipeline_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_pkey PRIMARY KEY (id);


--
-- Name: pipelines pipelines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_pkey PRIMARY KEY (id);


--
-- Name: run_logs run_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_logs
    ADD CONSTRAINT run_logs_pkey PRIMARY KEY (id);


--
-- Name: run_metrics run_metrics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_metrics
    ADD CONSTRAINT run_metrics_pkey PRIMARY KEY (id);


--
-- Name: runs runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pkey PRIMARY KEY (id);


--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (id);


--
-- Name: connections uq_connection_ws_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connections
    ADD CONSTRAINT uq_connection_ws_name UNIQUE (workspace_id, name);


--
-- Name: memberships uq_membership_ws_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT uq_membership_ws_user UNIQUE (workspace_id, user_id);


--
-- Name: pipeline_versions uq_pipeline_version_num; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT uq_pipeline_version_num UNIQUE (pipeline_id, version);


--
-- Name: pipelines uq_pipeline_ws_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT uq_pipeline_ws_name UNIQUE (workspace_id, name);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: workspaces workspaces_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_pkey PRIMARY KEY (id);


--
-- Name: workspaces workspaces_slug_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.workspaces
    ADD CONSTRAINT workspaces_slug_key UNIQUE (slug);


--
-- Name: ix_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_actor ON public.audit_log USING btree (actor_user_id, created_at);


--
-- Name: ix_audit_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_resource ON public.audit_log USING btree (resource_type, resource_id);


--
-- Name: ix_audit_workspace_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_audit_workspace_created ON public.audit_log USING btree (workspace_id, created_at);


--
-- Name: ix_connections_workspace_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_connections_workspace_id ON public.connections USING btree (workspace_id);


--
-- Name: ix_memberships_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memberships_user_id ON public.memberships USING btree (user_id);


--
-- Name: ix_memberships_workspace_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_memberships_workspace_id ON public.memberships USING btree (workspace_id);


--
-- Name: ix_pat_prefix; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pat_prefix ON public.personal_access_tokens USING btree (prefix);


--
-- Name: ix_pat_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pat_user_id ON public.personal_access_tokens USING btree (user_id);


--
-- Name: ix_pipeline_versions_pipeline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_versions_pipeline_id ON public.pipeline_versions USING btree (pipeline_id);


--
-- Name: ix_pipelines_workspace_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipelines_workspace_id ON public.pipelines USING btree (workspace_id);


--
-- Name: ix_run_logs_run_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_run_logs_run_ts ON public.run_logs USING btree (run_id, ts);


--
-- Name: ix_run_metrics_run_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_run_metrics_run_name ON public.run_metrics USING btree (run_id, name);


--
-- Name: ix_runs_heartbeat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_heartbeat ON public.runs USING btree (heartbeat_at);


--
-- Name: ix_runs_queue_poll; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_queue_poll ON public.runs USING btree (status, scheduled_at);


--
-- Name: ix_runs_workspace_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_runs_workspace_created ON public.runs USING btree (workspace_id, created_at);


--
-- Name: ix_schedules_pipeline_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_schedules_pipeline_id ON public.schedules USING btree (pipeline_id);


--
-- Name: audit_log audit_log_actor_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_actor_user_id_fkey FOREIGN KEY (actor_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audit_log audit_log_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT audit_log_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE SET NULL;


--
-- Name: connections connections_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connections
    ADD CONSTRAINT connections_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: connections connections_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connections
    ADD CONSTRAINT connections_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;


--
-- Name: cursors cursors_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cursors
    ADD CONSTRAINT cursors_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;


--
-- Name: memberships memberships_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: memberships memberships_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memberships
    ADD CONSTRAINT memberships_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;


--
-- Name: personal_access_tokens personal_access_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.personal_access_tokens
    ADD CONSTRAINT personal_access_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: pipeline_versions pipeline_versions_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: pipeline_versions pipeline_versions_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_versions
    ADD CONSTRAINT pipeline_versions_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES public.pipelines(id) ON DELETE CASCADE;


--
-- Name: pipelines pipelines_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: pipelines pipelines_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipelines
    ADD CONSTRAINT pipelines_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;


--
-- Name: run_logs run_logs_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_logs
    ADD CONSTRAINT run_logs_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;


--
-- Name: run_metrics run_metrics_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.run_metrics
    ADD CONSTRAINT run_metrics_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.runs(id) ON DELETE CASCADE;


--
-- Name: runs runs_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES public.pipelines(id) ON DELETE CASCADE;


--
-- Name: runs runs_pipeline_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_pipeline_version_id_fkey FOREIGN KEY (pipeline_version_id) REFERENCES public.pipeline_versions(id) ON DELETE RESTRICT;


--
-- Name: runs runs_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.schedules(id) ON DELETE SET NULL;


--
-- Name: runs runs_triggered_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_triggered_by_user_id_fkey FOREIGN KEY (triggered_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: runs runs_workspace_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.runs
    ADD CONSTRAINT runs_workspace_id_fkey FOREIGN KEY (workspace_id) REFERENCES public.workspaces(id) ON DELETE CASCADE;


--
-- Name: schedules schedules_created_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_created_by_user_id_fkey FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: schedules schedules_pipeline_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pipeline_id_fkey FOREIGN KEY (pipeline_id) REFERENCES public.pipelines(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
