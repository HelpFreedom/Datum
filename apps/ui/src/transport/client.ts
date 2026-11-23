/**
 * Запросы к серверу. Единственное место, где проставляются заголовки охраны.
 *
 * Их два, и оба обязательны по разным причинам. `X-Datum-Token` доказывает, что
 * запрос идёт от того, кто прочитал файл рукопожатия. `X-Datum-Client` на
 * изменяющих запросах превращает их из «простых» в требующие предварительной
 * проверки CORS — той самой, на которую сервер не отвечает, — поэтому
 * межсайтовая отправка формы до обработчика не доходит.
 */
import { endpoint } from "./endpoint";

export class ServerError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ServerError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { token } = await endpoint();
  const mutating = (init.method ?? "GET").toUpperCase() !== "GET";
  const response = await fetch(path, {
    ...init,
    headers: {
      ...init.headers,
      "X-Datum-Token": token,
      // Подпись для ленты: человек должен видеть, что сделал он, а что агент.
      // Ничего не разрешает — только подписывает.
      "X-Datum-Actor": "user",
      ...(mutating
        ? { "X-Datum-Client": "1", "Content-Type": "application/json" }
        : {}),
    },
  });
  if (!response.ok) {
    let text = `сервер ответил ${response.status}`;
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) text = body.error;
    } catch {
      /* тело не JSON — оставим код */
    }
    throw new ServerError(response.status, text);
  }
  return (await response.json()) as T;
}

// ---------------------------------------------------------------- операции
export interface JobResult<T = Record<string, unknown>> {
  job: string;
  status: "done" | "failed" | "cancelled" | "running" | "pending";
  result: T;
}

export const runOp = async <T = Record<string, unknown>>(
  name: string,
  args: Record<string, unknown> = {},
): Promise<JobResult<T>> => {
  const answer = await request<JobResult<T>>(`/api/ops/${encodeURIComponent(name)}`, {
    method: "POST",
    body: JSON.stringify(args),
  });
  // Сервер ждёт задание не бесконечно: по истечении срока результата ещё нет,
  // и раньше окно падало на `result.error` с «Cannot read properties of null».
  if (answer.result === null || answer.result === undefined) {
    throw new Error(`операция ${name} ещё идёт (задание ${answer.job}); результат придёт в ленту`);
  }
  return answer;
};

export interface JobRecord {
  id: string;
  op: string;
  actor: "user" | "agent" | "external";
  status: "done" | "failed" | "cancelled" | "running" | "pending";
  ms: number;
  started: number;
  finished: number;
  error?: string | null;
  /** Служебный вызов самого окна (отчёт о виде, крышка среза, чтение состояния). */
  service?: boolean;
}

/**
 * Лента — кратко: без результатов. Полный ответ включал результаты сотни
 * последних заданий, и перечитывался он на каждое событие задания.
 */
export const listJobs = () =>
  request<{ jobs: JobRecord[] }>("/api/jobs?brief=true").then((r) => r.jobs);

/** Отменить идущее или ждущее задание. Сервер снимает его процесс целиком. */
export const cancelJob = (id: string) =>
  request<{ job: string; cancelled: boolean; status: string }>(
    `/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });
