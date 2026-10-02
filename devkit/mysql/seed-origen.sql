-- Seed del ambiente de ORIGEN (dev, localhost:3306, schema `yappy`).
--
-- Es el estado "de producción": lo que el origen debería tener. El destino (qa)
-- se siembra aparte, con un desvío deliberado para que cada rama de
-- /api/compile tenga un caso real que resolver.
--
-- Aplicar:  docker exec -i yappy-mysql-dev mysql -uroot -p<PASS> yappy < devkit/mysql/seed-origen.sql

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- clientes -> idéntica en el destino. Rama `replace_in_a`: compila igual, con
-- `DROP TABLE IF EXISTS` adelante, así que no choca con la tabla que ya está.
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
-- pedidos -> al destino le falta la columna `canal` y el índice idx_pedidos_canal.
-- Rama `different`: ALTER TABLE ... ADD COLUMN.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS pedidos;
CREATE TABLE pedidos (
  pedido_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  cliente_id  BIGINT UNSIGNED NOT NULL,
  fecha       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  total       DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  estado      ENUM('pendiente','pagado','enviado','cancelado') NOT NULL DEFAULT 'pendiente',
  canal       VARCHAR(40)   NOT NULL DEFAULT 'web',
  PRIMARY KEY (pedido_id),
  KEY idx_pedidos_cliente (cliente_id),
  KEY idx_pedidos_canal (canal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO pedidos (cliente_id, total, estado, canal) VALUES
  (1, 1250.50, 'pagado',   'web'),
  (1,  340.00, 'pendiente', 'app'),
  (2,  899.99, 'enviado',  'web');

-- ---------------------------------------------------------------------------
-- lineas_pedido -> no existe en el destino. Rama `missing_in_a`: CREATE TABLE.
-- Lleva FK para que el passthrough de SHOW CREATE TABLE se vea completo.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS lineas_pedido;
CREATE TABLE lineas_pedido (
  linea_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  pedido_id    BIGINT UNSIGNED NOT NULL,
  producto     VARCHAR(160)  NOT NULL,
  cantidad     INT UNSIGNED   NOT NULL DEFAULT 1,
  precio_unit  DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  PRIMARY KEY (linea_id),
  KEY idx_lineas_pedido (pedido_id),
  CONSTRAINT fk_lineas_pedido FOREIGN KEY (pedido_id)
    REFERENCES pedidos (pedido_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO lineas_pedido (pedido_id, producto, cantidad, precio_unit) VALUES
  (1, 'Teclado mecanico', 1, 1250.50),
  (2, 'Mouse ergonomico', 2, 170.00);

-- ---------------------------------------------------------------------------
-- config_app -> misma estructura, distinto COMMENT de tabla. El DDL difiere pero
-- columnas e índices son idénticos: con el diff estructural esto salía vacío, y
-- con el reemplazo entero es el caso que lleva ese COMMENT al destino.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS config_app;
CREATE TABLE config_app (
  clave   VARCHAR(80) NOT NULL,
  valor   TEXT,
  PRIMARY KEY (clave)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Configuracion de la aplicacion';

INSERT INTO config_app (clave, valor) VALUES
  ('feature.checkout', 'true'),
  ('timeout.ms',       '30000');

-- ---------------------------------------------------------------------------
-- proc_calcular_total -> stored procedure con BEGIN...END, para ejercitar el
-- splitter de sentencias (exec.py: split_statements) y la rama de procedure.
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
   WHERE cliente_id = p_cliente_id;

  SET p_total = v_total;
END$$
DELIMITER ;
