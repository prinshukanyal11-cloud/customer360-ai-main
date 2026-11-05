import { describe, expect, it } from "vitest";
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import client from "@/services/api";

/**
 * Regression guard for the CSV upload bug.
 *
 * The axios instance used to declare `Content-Type: application/json` as a
 * default, which axios applies to every request. Its transformRequest then
 * serialises FormData instead of passing it through:
 *
 *     if (isFormData(data)) return hasJSONContentType
 *       ? JSON.stringify(formDataToJSON(data))
 *       : data;
 *
 * A File has no enumerable JSON properties, so the browser sent
 *
 *     Content-Type: application/json
 *     {"file":{}}
 *
 * with no boundary and no filename. FastAPI parsed the body as JSON and
 * answered 422 "Field required" for `file`, so the file never crossed the wire.
 *
 * The adapter is stubbed rather than pointed at a real socket: it captures the
 * payload *after* transformRequest, which is precisely where the two payloads
 * diverge, and it keeps the test independent of jsdom versus Node FormData
 * implementations.
 */
async function capture<T>(run: () => Promise<T>): Promise<{ contentType: string; data: unknown }> {
  const original = client.defaults.adapter;
  let contentType = "";
  let data: unknown;

  const adapter: AxiosAdapter = (config: InternalAxiosRequestConfig) => {
    const raw = config.headers.get("content-type");
    contentType = typeof raw === "string" ? raw : "";
    data = config.data;
    const response: AxiosResponse = {
      data: {},
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    };
    return Promise.resolve(response);
  };

  client.defaults.adapter = adapter;
  try {
    await run();
  } finally {
    // Always restore: a leaked stub would silently replace the real transport
    // for every test file sharing this module instance.
    client.defaults.adapter = original;
  }
  return { contentType, data };
}

describe("request encoding", () => {
  it("sends FormData as multipart, not a JSON object", async () => {
    const csv = "customer_id,name\n1,Alice\n";
    const form = new FormData();
    const file = new File([csv], "customers.csv", { type: "text/csv" });
    form.append("file", file);

    const { contentType, data } = await capture(() =>
      client.post("/datasets/upload", form),
    );

    // The regression itself: FormData must survive transformRequest untouched.
    expect(data).toBeInstanceOf(FormData);
    expect(contentType).not.toContain("application/json");

    // And the real file is inside it - not a serialised stand-in.
    const sent = (data as FormData).get("file") as File;
    expect(sent).toBeInstanceOf(File);
    expect(sent.name).toBe("customers.csv");
    // jsdom's File has no .text(); size is enough to prove the bytes travelled
    // instead of being flattened into {}.
    expect(sent.size).toBe(csv.length);
  });

  it("still sends plain objects as JSON", async () => {
    const { contentType, data } = await capture(() =>
      client.post("/auth/login", {
        email: "someone@example.com",
        password: "s3cret-pass",
      }),
    );

    expect(contentType).toContain("application/json");
    expect(JSON.parse(data as string)).toEqual({
      email: "someone@example.com",
      password: "s3cret-pass",
    });
  });
});
