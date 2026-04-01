-- PhoneBase · Инициализация БД
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS business;
CREATE SCHEMA IF NOT EXISTS personal_data;

CREATE ROLE app_user NOLOGIN;
GRANT USAGE ON SCHEMA business TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA business TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA business GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
CREATE ROLE pd_service NOLOGIN;
GRANT USAGE ON SCHEMA personal_data TO pd_service;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA personal_data TO pd_service;
ALTER DEFAULT PRIVILEGES IN SCHEMA personal_data GRANT SELECT, INSERT, UPDATE ON TABLES TO pd_service;
GRANT app_user TO phonebase;
GRANT pd_service TO phonebase;

CREATE TABLE business.stores (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL UNIQUE,
    city VARCHAR(100), address TEXT, is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE business.users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID REFERENCES business.stores(id),
    username VARCHAR(100) NOT NULL UNIQUE, full_name VARCHAR(255),
    role VARCHAR(20) NOT NULL CHECK (role IN ('admin','staff','info')),
    password_hash TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT true,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_login_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_users_store ON business.users(store_id);

CREATE OR REPLACE FUNCTION business.user_accessible_stores(p_user_id UUID)
RETURNS TABLE(store_id UUID) AS $$
    SELECT s.id FROM business.stores s
    JOIN business.users u ON u.id = p_user_id
    WHERE u.role = 'admin' OR s.id = u.store_id
$$ LANGUAGE SQL STABLE;

CREATE TABLE business.products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES business.stores(id),
    sku_1c VARCHAR(100) NOT NULL, brand VARCHAR(100), model VARCHAR(255) NOT NULL,
    storage VARCHAR(30), color VARCHAR(100), condition VARCHAR(50),
    battery_pct VARCHAR(10), in_repair BOOLEAN NOT NULL DEFAULT false,
    category VARCHAR(100), price_retail DECIMAL(12,2), price_cost DECIMAL(12,2),
    quantity INTEGER NOT NULL DEFAULT 0, is_sold BOOLEAN NOT NULL DEFAULT false,
    synced_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), UNIQUE(store_id, sku_1c)
);
CREATE INDEX idx_products_store ON business.products(store_id);
CREATE INDEX idx_products_brand ON business.products(brand, model);
CREATE INDEX idx_products_repair ON business.products(in_repair) WHERE in_repair=true;

CREATE TABLE business.product_photos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID NOT NULL REFERENCES business.products(id) ON DELETE CASCADE,
    uploaded_by UUID NOT NULL REFERENCES business.users(id),
    file_path TEXT NOT NULL, file_size INTEGER, is_main BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE business.purchase_docs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID NOT NULL REFERENCES business.products(id) ON DELETE CASCADE,
    uploaded_by UUID NOT NULL REFERENCES business.users(id),
    doc_type VARCHAR(50) NOT NULL CHECK (doc_type IN ('receipt','contract','passport_scan','other')),
    supplier_name VARCHAR(255), purchase_date DATE, purchase_price DECIMAL(12,2),
    has_personal_data BOOLEAN NOT NULL DEFAULT false, pd_record_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE business.price_history (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID NOT NULL REFERENCES business.products(id) ON DELETE CASCADE,
    our_price DECIMAL(12,2), avito_min DECIMAL(12,2), avito_avg DECIMAL(12,2),
    avito_max DECIMAL(12,2), avito_count INTEGER,
    recorded_at DATE NOT NULL DEFAULT CURRENT_DATE, UNIQUE(product_id, recorded_at)
);
CREATE TABLE business.import_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    store_id UUID NOT NULL REFERENCES business.stores(id),
    imported_by UUID NOT NULL REFERENCES business.users(id),
    filename VARCHAR(500),
    status VARCHAR(20) NOT NULL CHECK (status IN ('pending','processing','success','error')),
    items_total INTEGER DEFAULT 0, items_created INTEGER DEFAULT 0,
    items_updated INTEGER DEFAULT 0, items_sold INTEGER DEFAULT 0,
    error_message TEXT, started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), finished_at TIMESTAMPTZ
);

CREATE TABLE personal_data.client_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    full_name_enc BYTEA NOT NULL, passport_series_enc BYTEA,
    passport_number_enc BYTEA, issued_by_enc BYTEA, issued_date_enc BYTEA,
    consent_obtained_at TIMESTAMPTZ NOT NULL, consent_doc_path TEXT,
    doc_file_path TEXT, doc_file_hash TEXT, created_by_user_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scheduled_deletion_at TIMESTAMPTZ, deleted_at TIMESTAMPTZ
);
CREATE TABLE personal_data.access_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    record_id UUID NOT NULL REFERENCES personal_data.client_records(id),
    user_id UUID NOT NULL,
    action VARCHAR(30) NOT NULL CHECK (action IN ('view','download','update','delete_scheduled','deleted')),
    ip_address INET, user_agent TEXT, reason TEXT,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_pd_log_record ON personal_data.access_log(record_id, accessed_at DESC);
REVOKE DELETE ON personal_data.access_log FROM pd_service;
