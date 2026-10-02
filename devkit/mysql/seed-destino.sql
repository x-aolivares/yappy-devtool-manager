-- Seed del ambiente de DESTINO (qa, localhost:3307, schema `yappy`).
--
-- Mismo origen, DESVÍO deliberado. Cada diferencia cubre una rama de /api/compile:
--
--   | objeto           | desvío                                | rama              | script        |
--   |------------------|---------------------------------------|-------------------|---------------|
--   | clientes         | ninguno                               | equal             | null          |
--   | pedidos          | falta `canal` + `idx_pedidos_canal`   | different         | ALTER TABLE   |
--   | config_app       | solo el COMMENT de tabla              | different         | "" (vacío)    |
--   | lineas_pedido    | no existe                            | missing_in_a      | CREATE TABLE  |
--   | auditoria        | existe solo acá                       | none              | null          |
--   | proc_...         | cuerpo levemente distinto             | different         | CREATE OR REPL |
--
-- Aplicar:  docker exec -i yappy-mysql-qa mysql -uroot -p<PASS> yappy < devkit/mysql/seed-destino.sql

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- clientes -> idéntica al origen (copia literal, mismo orden de columnas).
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS clientes;
CREATE TABLE clientes (
  cliente_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  nombre       VARCHAR(120)  NOT NULL,
  apellido     VARCHAR(120)  NOT NULL,
  email        VARCHAR(180)  NOT NULL,
  fecha_alta   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  activo       TINYINT(1)    NOT NULL DEFAULT 1,
  PRIMARY KEY (cliente_id),
  UNIQUE KEY uk_clientes_email (email),
  KEY idx_clientes_apellido (apellido)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Maestro de clientes';

INSERT INTO clientes (nombre, apellido, email) VALUES
  ('Ana',    'Gomez',    'ana.gomez@example.com'),
  ('Bruno',  'Diaz',     'bruno.diaz@example.com'),
  ('Carla',  'Nunez',    'carla.nunez@example.com');

-- ---------------------------------------------------------------------------
-- pedidos -> DESVÍO: sin la columna `canal` ni el índice idx_pedidos_canal.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS pedidos;
CREATE TABLE pedidos (
  pedido_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  cliente_id  BIGINT UNSIGNED NOT NULL,
  fecha       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  total       DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  estado      ENUM('pendiente','pagado','enviado','cancelado') NOT NULL DEFAULT 'pendiente',
  PRIMARY KEY (pedido_id),
  KEY idx_pedidos_cliente (cliente_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO pedidos (cliente_id, total, estado) VALUES
  (1, 1250.50, 'pagado'),
  (1,  340.00, 'pendiente'),
  (2,  899.99, 'enviado');

-- ---------------------------------------------------------------------------
-- config_app -> misma estructura, COMMENT de tabla VIEJO. DDL distinto,
-- columnas e índices idénticos -> el script sale vacío.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS config_app;
CREATE TABLE config_app (
  clave   VARCHAR(80) NOT NULL,
  valor   TEXT,
  PRIMARY KEY (clave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Config Vieja';

INSERT INTO config_app (clave, valor) VALUES
  ('feature.checkout', 'true'),
  ('timeout.ms',       '30000');

-- ---------------------------------------------------------------------------
-- auditoria -> existe SOLO en el destino. No está en el origen -> status `none`.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS auditoria;
CREATE TABLE auditoria (
  auditoria_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  entidad      VARCHAR(80)  NOT NULL,
  accion       VARCHAR(40)  NOT NULL,
  usuario      VARCHAR(120) NOT NULL,
  momento      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (auditoria_id),
  KEY idx_auditoria_entidad (entidad)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO auditoria (entidad, accion, usuario) VALUES
  ('pedidos', 'crear', 'ana.gomez@example.com');

-- ---------------------------------------------------------------------------
-- proc_calcular_total -> cuerpo LEYMENTE distinto (filtra por estado).
-- ---------------------------------------------------------------------------
DROP PROCEDURE IF EXISTS proc_calcular_total;
DELIMITER $$
CREATE PROCEDURE proc_calcular_total(
  IN  p_cliente_id BIGINT UNSIGNED,
  OUT p_total      DECIMAL(12,2)
)
BEGIN
  DECLARE v_total DECIMAL(12,2) DEFAULT 0.00;

  SELECT COALESCE(SUM(total), 0)
    INTO v_total
    FROM pedidos
   WHERE cliente_id = p_cliente_id
     AND estado IN ('pagado', 'enviado');

  SET p_total = v_total;
END$$
DELIMITER ;
