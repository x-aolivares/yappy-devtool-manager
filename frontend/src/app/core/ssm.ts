/**
 * Los tipos de parámetro que acepta SSM.
 *
 * El backend los limita a estos tres, y el cliente generado los tipa como
 * unión. Un `value_type` que venga de la API —o de un environment viejo— hay
 * que narrowearlo antes de mandarlo: AWS rechaza el parámetro entero si el
 * tipo no existe, así que un valor suelto no es un detalle de tipos sino una
 * escritura que no se hace.
 */

export type SsmValueType = 'String' | 'StringList' | 'SecureString';

const SSM_VALUE_TYPES: readonly SsmValueType[] = ['String', 'StringList', 'SecureString'];

/** Narrowea un `value_type` a los que SSM conoce, con `String` por defecto. */
export function toValueType(value: string | null | undefined): SsmValueType {
  return SSM_VALUE_TYPES.includes(value as SsmValueType)
    ? (value as SsmValueType)
    : 'String';
}
