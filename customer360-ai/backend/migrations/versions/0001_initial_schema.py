"""initial schema

Revision ID: 0001
Create Date: 2026-09-24
"""
from __future__ import annotations

import sqlalchemy as sa  # noqa: F401
from alembic import op
from sqlalchemy.dialects import postgresql  # noqa: F401

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.execute(sa.text('''
    CREATE TABLE organizations (
        id SERIAL NOT NULL, 
        name VARCHAR(255) NOT NULL, 
        slug VARCHAR(120) NOT NULL, 
        status VARCHAR(32) DEFAULT 'ACTIVE' NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_organizations PRIMARY KEY (id)
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE customers (
        id SERIAL NOT NULL, 
        external_customer_id VARCHAR(255) NOT NULL, 
        name VARCHAR(255), 
        email VARCHAR(320), 
        phone VARCHAR(64), 
        location VARCHAR(255), 
        age INTEGER, 
        gender VARCHAR(32), 
        signup_date DATE, 
        acquisition_channel VARCHAR(64), 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_customers PRIMARY KEY (id), 
        CONSTRAINT uq_customers_org_extid UNIQUE (organization_id, external_customer_id), 
        CONSTRAINT fk_customers_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE products (
        id SERIAL NOT NULL, 
        external_product_id VARCHAR(255) NOT NULL, 
        product_name VARCHAR(255) NOT NULL, 
        category VARCHAR(128), 
        subcategory VARCHAR(128), 
        unit_price NUMERIC(14, 2), 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_products PRIMARY KEY (id), 
        CONSTRAINT uq_products_org_extid UNIQUE (organization_id, external_product_id), 
        CONSTRAINT fk_products_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE segments (
        id SERIAL NOT NULL, 
        type VARCHAR(16) NOT NULL, 
        name VARCHAR(128) NOT NULL, 
        description TEXT, 
        rules_json JSON, 
        color VARCHAR(32), 
        active BOOLEAN DEFAULT 'true' NOT NULL, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_segments PRIMARY KEY (id), 
        CONSTRAINT fk_segments_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE users (
        id SERIAL NOT NULL, 
        organization_id INTEGER, 
        email VARCHAR(320) NOT NULL, 
        full_name VARCHAR(255), 
        password_hash VARCHAR(255) NOT NULL, 
        role VARCHAR(32) DEFAULT 'VIEWER' NOT NULL, 
        is_active BOOLEAN DEFAULT 'true' NOT NULL, 
        last_login_at TIMESTAMP WITH TIME ZONE, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_users PRIMARY KEY (id), 
        CONSTRAINT fk_users_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE SET NULL
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE audit_logs (
        id SERIAL NOT NULL, 
        organization_id BIGINT, 
        user_id INTEGER, 
        action VARCHAR(128) NOT NULL, 
        entity_type VARCHAR(64), 
        entity_id BIGINT, 
        ip_address VARCHAR(64), 
        user_agent VARCHAR(512), 
        metadata_json JSON, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        CONSTRAINT pk_audit_logs PRIMARY KEY (id), 
        CONSTRAINT fk_audit_logs_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE SET NULL, 
        CONSTRAINT fk_audit_logs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE campaigns (
        id SERIAL NOT NULL, 
        name VARCHAR(255) NOT NULL, 
        objective VARCHAR(255), 
        offer VARCHAR(255), 
        channel VARCHAR(64), 
        audience_filter_json JSON, 
        start_date DATE, 
        end_date DATE, 
        status VARCHAR(32) DEFAULT 'DRAFT' NOT NULL, 
        target_count BIGINT, 
        cost_est NUMERIC(14, 2), 
        assumptions_json JSON, 
        created_by INTEGER, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_campaigns PRIMARY KEY (id), 
        CONSTRAINT fk_campaigns_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
        CONSTRAINT fk_campaigns_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE customer_segments (
        id SERIAL NOT NULL, 
        customer_id INTEGER NOT NULL, 
        segment_id INTEGER NOT NULL, 
        assigned_at TIMESTAMP WITH TIME ZONE, 
        confidence FLOAT, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_customer_segments PRIMARY KEY (id), 
        CONSTRAINT fk_customer_segments_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_customer_segments_segment_id_segments FOREIGN KEY(segment_id) REFERENCES segments (id) ON DELETE CASCADE, 
        CONSTRAINT fk_customer_segments_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE datasets (
        id SERIAL NOT NULL, 
        name VARCHAR(255) NOT NULL, 
        description TEXT, 
        source_type VARCHAR(32) DEFAULT 'csv' NOT NULL, 
        status VARCHAR(32) DEFAULT 'UPLOADED' NOT NULL, 
        total_rows BIGINT, 
        columns_json JSON, 
        created_by INTEGER, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_datasets PRIMARY KEY (id), 
        CONSTRAINT uq_datasets_org_name UNIQUE (organization_id, name), 
        CONSTRAINT fk_datasets_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
        CONSTRAINT fk_datasets_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE models (
        id SERIAL NOT NULL, 
        name VARCHAR(128) NOT NULL, 
        model_type VARCHAR(32) NOT NULL, 
        family VARCHAR(64), 
        status VARCHAR(32) DEFAULT 'TRAINED' NOT NULL, 
        description TEXT, 
        created_by INTEGER, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_models PRIMARY KEY (id), 
        CONSTRAINT fk_models_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
        CONSTRAINT fk_models_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE transactions (
        id SERIAL NOT NULL, 
        customer_id INTEGER NOT NULL, 
        external_order_id VARCHAR(255) NOT NULL, 
        order_date DATE NOT NULL, 
        product_id INTEGER, 
        quantity INTEGER, 
        amount NUMERIC(14, 2) NOT NULL, 
        discount NUMERIC(14, 2), 
        currency VARCHAR(8), 
        channel VARCHAR(64), 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_transactions PRIMARY KEY (id), 
        CONSTRAINT uq_transactions_org_order UNIQUE (organization_id, external_order_id), 
        CONSTRAINT fk_transactions_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_transactions_product_id_products FOREIGN KEY(product_id) REFERENCES products (id) ON DELETE SET NULL, 
        CONSTRAINT fk_transactions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE campaign_members (
        id SERIAL NOT NULL, 
        campaign_id INTEGER NOT NULL, 
        customer_id INTEGER NOT NULL, 
        status VARCHAR(32) DEFAULT 'ELIGIBLE' NOT NULL, 
        assigned_at TIMESTAMP WITH TIME ZONE, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_campaign_members PRIMARY KEY (id), 
        CONSTRAINT fk_campaign_members_campaign_id_campaigns FOREIGN KEY(campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE, 
        CONSTRAINT fk_campaign_members_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_campaign_members_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE campaign_results (
        id SERIAL NOT NULL, 
        campaign_id INTEGER NOT NULL, 
        response_rate FLOAT, 
        conversion_rate FLOAT, 
        revenue FLOAT, 
        cost FLOAT, 
        roi FLOAT, 
        metrics_json JSON, 
        calculated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_campaign_results PRIMARY KEY (id), 
        CONSTRAINT fk_campaign_results_campaign_id_campaigns FOREIGN KEY(campaign_id) REFERENCES campaigns (id) ON DELETE CASCADE, 
        CONSTRAINT fk_campaign_results_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE dataset_versions (
        id SERIAL NOT NULL, 
        dataset_id INTEGER NOT NULL, 
        version_no INTEGER NOT NULL, 
        file_path VARCHAR(1024) NOT NULL, 
        checksum VARCHAR(128), 
        row_count BIGINT, 
        column_mapping_json JSON, 
        dq_score FLOAT, 
        dq_report_json JSON, 
        processed_at TIMESTAMP WITH TIME ZONE, 
        created_by INTEGER, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_dataset_versions PRIMARY KEY (id), 
        CONSTRAINT fk_dataset_versions_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE, 
        CONSTRAINT fk_dataset_versions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
        CONSTRAINT fk_dataset_versions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE customer_features (
        id SERIAL NOT NULL, 
        customer_id INTEGER NOT NULL, 
        dataset_version_id INTEGER NOT NULL, 
        recency_days FLOAT, 
        frequency FLOAT, 
        monetary FLOAT, 
        rfm_score VARCHAR(8), 
        rfm_segment VARCHAR(64), 
        aov FLOAT, 
        avg_days_between FLOAT, 
        tenure_days FLOAT, 
        category_diversity INTEGER, 
        discount_usage FLOAT, 
        engagement_score FLOAT, 
        clv_est FLOAT, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_customer_features PRIMARY KEY (id), 
        CONSTRAINT uq_cfeatures_cust_ver UNIQUE (organization_id, customer_id, dataset_version_id), 
        CONSTRAINT fk_customer_features_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_customer_features_dataset_version_id_dataset_versions FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions (id) ON DELETE CASCADE, 
        CONSTRAINT fk_customer_features_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE model_versions (
        id SERIAL NOT NULL, 
        model_id INTEGER NOT NULL, 
        version_no INTEGER NOT NULL, 
        artifact_path VARCHAR(1024) NOT NULL, 
        preprocessing_path VARCHAR(1024) NOT NULL, 
        features_list JSON, 
        params_json JSON, 
        metrics_json JSON, 
        train_dataset_version_id INTEGER, 
        random_seed INTEGER, 
        training_duration_s FLOAT, 
        trained_at TIMESTAMP WITH TIME ZONE, 
        active BOOLEAN DEFAULT 'false' NOT NULL, 
        created_by INTEGER, 
        organization_id BIGINT NOT NULL, 
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_model_versions PRIMARY KEY (id), 
        CONSTRAINT fk_model_versions_model_id_models FOREIGN KEY(model_id) REFERENCES models (id) ON DELETE CASCADE, 
        CONSTRAINT fk_model_versions_train_dataset_version_id_dataset_versions FOREIGN KEY(train_dataset_version_id) REFERENCES dataset_versions (id) ON DELETE SET NULL, 
        CONSTRAINT fk_model_versions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
        CONSTRAINT fk_model_versions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE predictions (
        id SERIAL NOT NULL, 
        model_version_id INTEGER NOT NULL, 
        customer_id INTEGER NOT NULL, 
        prediction_label BOOLEAN, 
        prediction_proba FLOAT NOT NULL, 
        risk_level VARCHAR(16), 
        score_payload JSON, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        inferred_at TIMESTAMP WITH TIME ZONE, 
        organization_id BIGINT NOT NULL, 
        CONSTRAINT pk_predictions PRIMARY KEY (id), 
        CONSTRAINT fk_predictions_model_version_id_model_versions FOREIGN KEY(model_version_id) REFERENCES model_versions (id) ON DELETE CASCADE, 
        CONSTRAINT fk_predictions_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_predictions_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE recommendations (
        id SERIAL NOT NULL, 
        customer_id INTEGER NOT NULL, 
        type VARCHAR(32) NOT NULL, 
        action VARCHAR(64) NOT NULL, 
        reason TEXT NOT NULL, 
        priority_score FLOAT, 
        confidence FLOAT, 
        expected_objective VARCHAR(255), 
        rules_triggered_json JSON, 
        model_version_id INTEGER, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        organization_id BIGINT NOT NULL, 
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
        CONSTRAINT pk_recommendations PRIMARY KEY (id), 
        CONSTRAINT fk_recommendations_customer_id_customers FOREIGN KEY(customer_id) REFERENCES customers (id) ON DELETE CASCADE, 
        CONSTRAINT fk_recommendations_model_version_id_model_versions FOREIGN KEY(model_version_id) REFERENCES model_versions (id) ON DELETE SET NULL, 
        CONSTRAINT fk_recommendations_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE TABLE explanations (
        id SERIAL NOT NULL, 
        prediction_id INTEGER NOT NULL, 
        method VARCHAR(32) DEFAULT 'SHAP' NOT NULL, 
        base_value FLOAT, 
        global_importance_json JSON, 
        local_contrib_json JSON, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
        organization_id BIGINT NOT NULL, 
        CONSTRAINT pk_explanations PRIMARY KEY (id), 
        CONSTRAINT fk_explanations_prediction_id_predictions FOREIGN KEY(prediction_id) REFERENCES predictions (id) ON DELETE CASCADE, 
        CONSTRAINT fk_explanations_organization_id_organizations FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
    )
    '''))

    op.execute(sa.text('''
    CREATE UNIQUE INDEX ix_organizations_slug ON organizations (slug)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customers_org_signup ON customers (organization_id, signup_date)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customers_organization_id ON customers (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_products_organization_id ON products (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_segments_organization_id ON segments (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_users_organization_id ON users (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE UNIQUE INDEX ix_users_email ON users (email)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_users_org_email ON users (organization_id, email)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_audit_logs_user_id ON audit_logs (user_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaigns_organization_id ON campaigns (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_segments_customer_id ON customer_segments (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_segments_segment_id ON customer_segments (segment_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_segments_organization_id ON customer_segments (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_datasets_organization_id ON datasets (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_models_organization_id ON models (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_transactions_org_date ON transactions (organization_id, order_date)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_transactions_org_customer ON transactions (organization_id, customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_transactions_organization_id ON transactions (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_transactions_customer_id ON transactions (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaign_members_organization_id ON campaign_members (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaign_members_campaign_id ON campaign_members (campaign_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaign_members_customer_id ON campaign_members (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaign_results_organization_id ON campaign_results (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_campaign_results_campaign_id ON campaign_results (campaign_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_dataset_versions_dataset_id ON dataset_versions (dataset_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_dataset_versions_organization_id ON dataset_versions (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_features_organization_id ON customer_features (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_features_dataset_version_id ON customer_features (dataset_version_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_customer_features_customer_id ON customer_features (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_model_versions_organization_id ON model_versions (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_model_versions_model_id ON model_versions (model_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_predictions_organization_id ON predictions (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_predictions_model_version_id ON predictions (model_version_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_predictions_customer_id ON predictions (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_recommendations_customer_id ON recommendations (customer_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_recommendations_organization_id ON recommendations (organization_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_explanations_prediction_id ON explanations (prediction_id)
    '''))

    op.execute(sa.text('''
    CREATE INDEX ix_explanations_organization_id ON explanations (organization_id)
    '''))



def downgrade() -> None:

    op.execute(sa.text('DROP TABLE IF EXISTS explanations CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS recommendations CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS predictions CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS model_versions CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS customer_features CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS dataset_versions CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS campaign_results CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS campaign_members CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS transactions CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS models CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS datasets CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS customer_segments CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS campaigns CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS audit_logs CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS users CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS segments CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS products CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS customers CASCADE'))

    op.execute(sa.text('DROP TABLE IF EXISTS organizations CASCADE'))

