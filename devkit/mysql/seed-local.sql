-- Seed del ambiente LOCAL (localhost:3308, schema `yappy`).
--
-- `local` es un ambiente de base de datos únicamente: se alcanza por TCP directo
-- contra el contenedor yappy-mysql-local y NO es un ambiente de AWS (no hay
-- Parameter Store ni Secrets Manager). Sirve para probar consultas, migraciones y
-- diffs de esquema contra datos realistas sin tocar nada real.
--
-- Contenido: los mismos objetos que usan dev/qa (clientes, pedidos,
-- lineas_pedido, config_app, proc_calcular_total) para que un diff contra cualquiera
-- de los dos se vea con la misma forma, más la tabla `orders` que es la que se
-- consulta desde la página de SQL (`SELECT * FROM yappy.orders`).
--
-- Aplicar:  docker exec -i yappy-mysql-local mysql -uroot -p<PASS> yappy < devkit/mysql/seed-local.sql

SET NAMES utf8mb4;

-- ---------------------------------------------------------------------------
-- Reset, para que el seed se pueda volver a correr. Primero se borran TODAS las
-- tablas, hijas antes que padres: MySQL no deja dropear una tabla que otra
-- referencia (error 3730) y el `IF EXISTS` de cada sección no lo evita, porque
-- solo silencia el 1051 de "la tabla no existe".
--
-- `orders` entra igual aunque hoy no declare FKs: una base sembrada con la
-- versión anterior de este seed sí las tenía, y sin esta línea el DROP de
-- `clientes` de la línea siguiente moría con 3730.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS lineas_pedido;
DROP TABLE IF EXISTS pedidos;
DROP TABLE IF EXISTS clientes;
DROP TABLE IF EXISTS config_app;

-- ---------------------------------------------------------------------------
-- clientes -> idéntica a dev/qa. Rama `equal` en un diff contra cualquiera.
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
-- pedidos -> idéntica a dev (con `canal` y su índice).
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
  (1, 1250.50, 'pagado',    'web'),
  (1,  340.00, 'pendiente', 'app'),
  (2,  899.99, 'enviado',   'web');

-- ---------------------------------------------------------------------------
-- lineas_pedido -> idéntica a dev, con su FK.
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
-- config_app -> misma estructura y mismo COMMENT que dev.
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
-- orders -> la tabla que se consulta desde la página de SQL. No existe en dev/qa:
-- es el objeto propio de `local`, para que haya algo real que mirar sin AWS.
--
-- `customer_id` NO lleva FOREIGN KEY, a propósito. La relación con `clientes`
-- es lógica y se resuelve en los queries, que es como trabajan los ambientes
-- reales. Con la FK declarada, compilar `yappy.clientes` en `local` no tenía
-- salida: el destino la referencia, /api/compile lo rechaza, y no había forma
-- de dejar `clientes` como estaba. El índice sí queda, que es lo que hace útil
-- el JOIN.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS orders;
CREATE TABLE orders (
  order_id     BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  customer_id  BIGINT UNSIGNED NOT NULL,
  order_date   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status       ENUM('NEW','PAID','SHIPPED','CANCELLED') NOT NULL DEFAULT 'NEW',
  total_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
  channel      VARCHAR(40)   NOT NULL DEFAULT 'web',
  PRIMARY KEY (order_id),
  KEY idx_orders_customer (customer_id),
  KEY idx_orders_status (status),
  KEY idx_orders_date (order_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='Pedidos en ingles (solo local)';

INSERT INTO orders (customer_id, status, total_amount, channel) VALUES
  (1, 'PAID',     1250.50, 'web'),
  (1, 'NEW',       340.00, 'app'),
  (2, 'SHIPPED',   899.99, 'web'),
  (3, 'CANCELLED',  75.25, 'web');

-- ---------------------------------------------------------------------------
-- proc_calcular_total -> misma firma que dev/qa; suma todos los pedidos del cliente.
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