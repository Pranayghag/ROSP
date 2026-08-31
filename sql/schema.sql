-- ---------------------------------------------------------------------------
-- CampusCare database schema (MySQL / MariaDB)
--
-- GENERATED FILE -- do not edit by hand.
-- Regenerate with:  python scripts/dump_schema.py
-- The authoritative definitions live in app/models.py
--
-- To create everything from scratch without running the application:
--   mysql -u root -p < sql/schema.sql
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS `rosp`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `rosp`;

SET FOREIGN_KEY_CHECKS = 0;


-- categories ----------------------------------------------------------
CREATE TABLE categories (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	description VARCHAR(255), 
	is_active BOOL NOT NULL, 
	sla_hours INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- locations -----------------------------------------------------------
CREATE TABLE locations (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	building VARCHAR(120), 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- users ---------------------------------------------------------------
CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	name VARCHAR(120) NOT NULL, 
	email VARCHAR(190) NOT NULL, 
	password_hash VARCHAR(255) NOT NULL, 
	`role` VARCHAR(20) NOT NULL, 
	department VARCHAR(120), 
	roll_no VARCHAR(50), 
	created_at DATETIME NOT NULL, 
	phone VARCHAR(30), 
	designation VARCHAR(60), 
	staff_id VARCHAR(50), 
	authorization_status VARCHAR(20) NOT NULL, 
	authorized_by INTEGER, 
	authorized_at DATETIME, 
	authorization_note VARCHAR(500), 
	password_set BOOL NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(authorized_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_users_authorization_status ON users (authorization_status);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX ix_users_role ON users (`role`);

-- complaints ----------------------------------------------------------
CREATE TABLE complaints (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(20), 
	title VARCHAR(200) NOT NULL, 
	description TEXT NOT NULL, 
	category_id INTEGER NOT NULL, 
	location_id INTEGER NOT NULL, 
	priority VARCHAR(20) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	student_id INTEGER NOT NULL, 
	assigned_staff_id INTEGER, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	resolved_at DATETIME, 
	closed_at DATETIME, 
	due_at DATETIME, 
	escalated_at DATETIME, 
	resolution_note TEXT, 
	reopen_reason VARCHAR(500), 
	PRIMARY KEY (id), 
	FOREIGN KEY(category_id) REFERENCES categories (id), 
	FOREIGN KEY(location_id) REFERENCES locations (id), 
	FOREIGN KEY(student_id) REFERENCES users (id), 
	FOREIGN KEY(assigned_staff_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_complaints_assigned_staff_id ON complaints (assigned_staff_id);
CREATE UNIQUE INDEX ix_complaints_code ON complaints (code);
CREATE INDEX ix_complaints_status ON complaints (status);
CREATE INDEX ix_complaints_student_id ON complaints (student_id);

-- otp_codes -----------------------------------------------------------
CREATE TABLE otp_codes (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	otp_hash VARCHAR(255) NOT NULL, 
	purpose VARCHAR(30) NOT NULL, 
	expires_at DATETIME NOT NULL, 
	attempts INTEGER NOT NULL, 
	used BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	consumed_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_otp_codes_purpose ON otp_codes (purpose);
CREATE INDEX ix_otp_codes_used ON otp_codes (used);
CREATE INDEX ix_otp_codes_user_id ON otp_codes (user_id);

-- attachments ---------------------------------------------------------
CREATE TABLE attachments (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	complaint_id INTEGER NOT NULL, 
	file_name VARCHAR(255) NOT NULL, 
	file_path VARCHAR(255) NOT NULL, 
	file_type VARCHAR(100) NOT NULL, 
	file_size INTEGER NOT NULL, 
	uploaded_by INTEGER NOT NULL, 
	uploaded_at DATETIME NOT NULL, 
	attachment_type VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(complaint_id) REFERENCES complaints (id) ON DELETE CASCADE, 
	FOREIGN KEY(uploaded_by) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_attachments_attachment_type ON attachments (attachment_type);
CREATE INDEX ix_attachments_complaint_id ON attachments (complaint_id);

-- complaint_events ----------------------------------------------------
CREATE TABLE complaint_events (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	complaint_id INTEGER NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	note VARCHAR(500), 
	actor_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(complaint_id) REFERENCES complaints (id) ON DELETE CASCADE, 
	FOREIGN KEY(actor_id) REFERENCES users (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_complaint_events_complaint_id ON complaint_events (complaint_id);

-- email_logs ----------------------------------------------------------
CREATE TABLE email_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	to_address VARCHAR(190) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	template VARCHAR(60), 
	status VARCHAR(20) NOT NULL, 
	error VARCHAR(500), 
	user_id INTEGER, 
	complaint_id INTEGER, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(complaint_id) REFERENCES complaints (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_email_logs_created_at ON email_logs (created_at);
CREATE INDEX ix_email_logs_status ON email_logs (status);
CREATE INDEX ix_email_logs_to_address ON email_logs (to_address);

-- notifications -------------------------------------------------------
CREATE TABLE notifications (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	user_id INTEGER NOT NULL, 
	complaint_id INTEGER, 
	message VARCHAR(300) NOT NULL, 
	is_read BOOL NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id), 
	FOREIGN KEY(complaint_id) REFERENCES complaints (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX ix_notifications_complaint_id ON notifications (complaint_id);
CREATE INDEX ix_notifications_is_read ON notifications (is_read);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);

SET FOREIGN_KEY_CHECKS = 1;

