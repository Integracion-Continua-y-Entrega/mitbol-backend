"""Identidad compartida y núcleo de persistencia administrativa.

Revision ID: 20261105_0004
Revises: 20261102_0003

Sólo revisable/local-temporal en esta fase. No aplicar a Neon ni mitbol_dev.
No crea cuentas, empleados, credenciales PostgreSQL ni grants. Los dos roles
son roles de aplicación. downgrade elimina las siete tablas y sus datos.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261105_0004"
down_revision = "20261102_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "Persons",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_Persons_name_not_blank"),
        sa.CheckConstraint("length(btrim(phone)) > 0", name="ck_Persons_phone_not_blank"),
        sa.PrimaryKeyConstraint("person_id", name="pk_Persons"),
    )
    op.create_table(
        "PersonalAccounts",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(btrim(email)) AND position('@' in email) > 1",
                           name="ck_PersonalAccounts_normalized_email"),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name="ck_PersonalAccounts_argon2id"),
        sa.CheckConstraint("auth_version >= 0", name="ck_PersonalAccounts_auth_version"),
        sa.PrimaryKeyConstraint("account_id", name="pk_PersonalAccounts"),
        sa.UniqueConstraint("person_id", name="uq_PersonalAccounts_person_id"),
        sa.UniqueConstraint("email", name="uq_PersonalAccounts_email"),
        sa.ForeignKeyConstraint(["person_id"], ["Persons.person_id"], name="fk_PersonalAccounts_person_id"),
    )
    op.create_table(
        "Employees",
        sa.Column("person_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("employee_code", sa.String(30), nullable=False),
        sa.Column("hired_at", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(btrim(employee_code)) > 0", name="ck_Employees_code_not_blank"),
        sa.PrimaryKeyConstraint("person_id", name="pk_Employees"),
        sa.UniqueConstraint("employee_code", name="uq_Employees_employee_code"),
        sa.ForeignKeyConstraint(["person_id"], ["Persons.person_id"], name="fk_Employees_person_id"),
    )
    roles = op.create_table(
        "Roles",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{0,29}$'", name="ck_Roles_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_Roles_name_not_blank"),
        sa.PrimaryKeyConstraint("role_id", name="pk_Roles"),
        sa.UniqueConstraint("code", name="uq_Roles_code"),
    )
    op.create_table(
        "EmployeeRoles",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("person_id", "role_id", name="pk_EmployeeRoles"),
        sa.ForeignKeyConstraint(["person_id"], ["Employees.person_id"], name="fk_EmployeeRoles_person_id"),
        sa.ForeignKeyConstraint(["role_id"], ["Roles.role_id"], name="fk_EmployeeRoles_role_id"),
    )
    op.create_table(
        "AccountSessions",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token_hash", sa.String(64), nullable=True),
        sa.Column("auth_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(50), nullable=True),
        sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_AccountSessions_token_hash"),
        sa.CheckConstraint("csrf_token_hash IS NULL OR csrf_token_hash ~ '^[0-9a-f]{64}$'",
                           name="ck_AccountSessions_csrf_hash"),
        sa.CheckConstraint("auth_version >= 0", name="ck_AccountSessions_auth_version"),
        sa.CheckConstraint("created_at <= last_activity_at AND last_activity_at < idle_expires_at "
                           "AND idle_expires_at <= expires_at", name="ck_AccountSessions_expiration"),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= created_at", name="ck_AccountSessions_revocation"),
        sa.CheckConstraint("revoked_at IS NOT NULL OR revocation_reason IS NULL",
                           name="ck_AccountSessions_revocation_reason"),
        sa.PrimaryKeyConstraint("session_id", name="pk_AccountSessions"),
        sa.UniqueConstraint("token_hash", name="uq_AccountSessions_token_hash"),
        sa.ForeignKeyConstraint(["account_id"], ["PersonalAccounts.account_id"], name="fk_AccountSessions_account_id"),
    )
    op.create_index("ix_AccountSessions_account_id", "AccountSessions", ["account_id"])
    op.create_index("ix_AccountSessions_expires_at", "AccountSessions", ["expires_at"])
    op.create_table(
        "AuditEvents",
        sa.Column("audit_event_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_account_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("failure_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("outcome IN ('success', 'denied', 'failure')", name="ck_AuditEvents_outcome"),
        sa.CheckConstraint("action ~ '^[a-z][a-z0-9_.:]{0,99}$'", name="ck_AuditEvents_action"),
        sa.PrimaryKeyConstraint("audit_event_id", name="pk_AuditEvents"),
        sa.ForeignKeyConstraint(["actor_account_id"], ["PersonalAccounts.account_id"], name="fk_AuditEvents_actor_account_id"),
    )
    op.create_index("ix_AuditEvents_actor_created", "AuditEvents", ["actor_account_id", "created_at"])
    # Roles de aplicación únicamente; sin personas, cuentas ni contraseñas.
    op.bulk_insert(roles, [
        {"code": "admin", "name": "Admin", "description": "Administración autorizada de MITBOL."},
        {"code": "staff", "name": "Staff", "description": "Consulta administrativa y disponibilidad local."},
    ])


def downgrade() -> None:
    op.drop_index("ix_AuditEvents_actor_created", table_name="AuditEvents")
    op.drop_table("AuditEvents")
    op.drop_index("ix_AccountSessions_expires_at", table_name="AccountSessions")
    op.drop_index("ix_AccountSessions_account_id", table_name="AccountSessions")
    op.drop_table("AccountSessions")
    op.drop_table("EmployeeRoles")
    op.drop_table("Roles")
    op.drop_table("Employees")
    op.drop_table("PersonalAccounts")
    op.drop_table("Persons")
