export interface CostProofClientOptions {
  baseUrl?: string;
  apiKey?: string;
  fetchImpl?: typeof fetch;
}

export type JsonObject = Record<string, unknown>;

export class CostProofClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: CostProofClientOptions = {}) {
    this.baseUrl = (options.baseUrl ?? "http://localhost:4001").replace(/\/+$/, "");
    this.apiKey = options.apiKey;
    this.fetchImpl = options.fetchImpl ?? fetch;
  }

  health(): Promise<JsonObject> {
    return this.getJson("/health");
  }

  spendSummary(): Promise<JsonObject> {
    return this.getJson("/api/spend/summary");
  }

  async routingDecisions(limit = 100): Promise<JsonObject[]> {
    const payload = await this.getJson(`/api/routing/decisions?limit=${limit}`);
    const decisions = payload.decisions;
    return Array.isArray(decisions) ? decisions.filter(isJsonObject) : [];
  }

  private async getJson(path: string): Promise<JsonObject> {
    const headers: Record<string, string> = {};
    if (this.apiKey) {
      headers.authorization = `Bearer ${this.apiKey}`;
    }

    const response = await this.fetchImpl(`${this.baseUrl}${path}`, { headers });
    if (!response.ok) {
      throw new Error(`CostProof request failed with HTTP ${response.status}`);
    }

    const payload: unknown = await response.json();
    if (!isJsonObject(payload)) {
      throw new Error("CostProof server returned an unexpected response shape");
    }
    return payload;
  }
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
