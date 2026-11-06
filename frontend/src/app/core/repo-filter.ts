import type { DeploymentRepoInfo } from '../api-gen/models';

/**
 * Normaliza para comparar: minúsculas y sin espacios.
 *
 * Sin espacios para que `m a n` y `man` digan lo mismo. Así escribir letra por
 * letra, con o sin separación, va cerrando la lista del mismo modo.
 */
function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, '');
}

/**
 * Los repos cuyo nombre contiene lo escrito, en memoria.
 *
 * Coincidencia por subcadena en el slug y en el nombre visible. Los que
 * **empiezan** con lo escrito van primero, porque es lo que el usuario busca
 * cuando tipea el comienzo de un nombre. Un texto vacío no devuelve nada: la
 * lista sólo aparece cuando hay algo escrito.
 */
export function matchRepos(
  repos: readonly DeploymentRepoInfo[],
  query: string,
  limit = 50,
): DeploymentRepoInfo[] {
  const needle = normalize(query);
  if (!needle) return [];

  const prefix: DeploymentRepoInfo[] = [];
  const inside: DeploymentRepoInfo[] = [];
  for (const repo of repos) {
    const slug = normalize(repo.slug);
    const name = normalize(repo.name);
    if (slug.startsWith(needle) || name.startsWith(needle)) {
      prefix.push(repo);
    } else if (slug.includes(needle) || name.includes(needle)) {
      inside.push(repo);
    }
  }
  return [...prefix, ...inside].slice(0, limit);
}
