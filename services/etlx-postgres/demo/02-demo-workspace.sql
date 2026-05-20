-- Demo data — applied ONLY when the image is built with
-- `--build-arg INCLUDE_DEMO_SEED=1`. Suitable for tutorials, screencasts,
-- and "five-minute first run" demos. NEVER ship this for production —
-- the admin password is well-known.
--
-- After first boot you can immediately log in:
--   email    : demo@example.com
--   password : demopass
--   workspace: demo (slug)
--
-- The bcrypt hash below is the cost-12 hash of "demopass". Generated
-- via `etlx-server admin create-user --email demo@example.com
-- --name Demo --password demopass`, then extracted from the users
-- table. Regenerate if you change the cost factor in PasswordService.

BEGIN;

-- pragma: allowlist secret  (the hash + password are demo-only and well-known)
INSERT INTO users (id, email, name, auth_method, password_hash, is_superadmin, created_at, updated_at)
VALUES (
    '00000000-0000-7000-8000-000000000001',
    'demo@example.com',
    'Demo',
    'local',
    '$2b$12$8k.DrGE1j6OZhfxphqgn9.TdS6R2ee5F3zWDkihXu312BvFwq8tee',
    TRUE,
    NOW(),
    NOW()
);

INSERT INTO workspaces (id, name, slug, color_hex, created_at, updated_at)
VALUES (
    '00000000-0000-7000-8000-000000000002',
    'Demo',
    'demo',
    '#FF3D8B',
    NOW(),
    NOW()
);

INSERT INTO memberships (id, workspace_id, user_id, role, created_at, updated_at)
VALUES (
    '00000000-0000-7000-8000-000000000003',
    '00000000-0000-7000-8000-000000000002',
    '00000000-0000-7000-8000-000000000001',
    'owner',
    NOW(),
    NOW()
);

COMMIT;
