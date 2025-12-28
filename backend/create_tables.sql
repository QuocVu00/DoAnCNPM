CREATE TABLE IF NOT EXISTS residents (
  id INT AUTO_INCREMENT PRIMARY KEY,
  full_name VARCHAR(100) NOT NULL,
  floor TINYINT,
  room VARCHAR(10),
  cccd VARCHAR(20),
  email VARCHAR(100),
  phone VARCHAR(20),
  status ENUM('active','inactive') DEFAULT 'active',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS resident_vehicles (
  id INT AUTO_INCREMENT PRIMARY KEY,
  resident_id INT NOT NULL,
  plate VARCHAR(20) NOT NULL,
  vehicle_type ENUM('car','motorbike','other') DEFAULT 'motorbike',
  is_in_parking BOOLEAN DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (resident_id) REFERENCES residents(id)
);

CREATE TABLE IF NOT EXISTS resident_backup_codes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  resident_id INT NOT NULL,
  backup_code VARCHAR(16) NOT NULL,
  is_active BOOLEAN DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (resident_id) REFERENCES residents(id)
);

CREATE TABLE IF NOT EXISTS guest_sessions (
  id INT AUTO_INCREMENT PRIMARY KEY,
  plate VARCHAR(20) NOT NULL,
  ticket_code CHAR(6) NOT NULL,
  checkin_time DATETIME NOT NULL,
  checkout_time DATETIME NULL,
  fee INT DEFAULT 0,
  entry_image_path VARCHAR(255),
  exit_image_path VARCHAR(255),
  status ENUM('open','closed') DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS parking_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  event_time DATETIME NOT NULL,
  event_type ENUM('resident_in','resident_out','guest_in','guest_out') NOT NULL,
  user_type ENUM('resident','guest') NOT NULL,
  resident_id INT NULL,
  guest_session_id INT NULL,
  plate VARCHAR(20),
  FOREIGN KEY (resident_id) REFERENCES residents(id),
  FOREIGN KEY (guest_session_id) REFERENCES guest_sessions(id)
);

CREATE TABLE IF NOT EXISTS admin_users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  username VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  role ENUM('admin','staff') DEFAULT 'admin',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
