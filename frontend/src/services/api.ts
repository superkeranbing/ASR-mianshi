import type { Recording, Transcript, Resume, InterviewReport } from "../types";

const BASE = "/api";

function getToken(): string | null {
  try {
    const stored = localStorage.getItem("auth_token");
    return stored || null;
  } catch {
    return null;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (options?.headers) {
    if (options.headers instanceof Headers) {
      options.headers.forEach((v, k) => { headers[k] = v; });
    } else if (Array.isArray(options.headers)) {
      options.headers.forEach(([k, v]) => { headers[k] = v; });
    } else {
      Object.assign(headers, options.headers);
    }
  }
  if (!headers["Content-Type"] && !(options?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers["Authorization"] = "Bearer " + token;
  }
  const res = await fetch(BASE + path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Request failed" }));
    throw new Error(err.detail);
  }
  return res.json();
}

// Auth
export function register(username: string, password: string) {
  return request<{ access_token: string; user: { id: string; username: string } }>(
    "/auth/register",
    { method: "POST", body: JSON.stringify({ username, password }) }
  );
}

export function login(username: string, password: string) {
  return request<{ access_token: string; user: { id: string; username: string } }>(
    "/auth/login",
    { method: "POST", body: JSON.stringify({ username, password }) }
  );
}

// Recordings
export function listRecordings() {
  return request<Recording[]>("/recordings");
}

export function getRecording(id: string) {
  return request<Recording & { transcripts: Transcript[] }>("/recordings/" + id);
}

export function uploadRecording(file: File, title: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("title", title);
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = "Bearer " + token;
  return fetch(BASE + "/recordings/upload", { method: "POST", body: fd, headers }).then((r) => r.json());
}

export function triggerTranscribe(id: string) {
  return request<{ status: string; task_id?: string; message?: string }>("/asr/" + id + "/transcribe", { method: "POST" });
}

export function getRecordingStatus(id: string) {
  return request<{ status: string }>("/asr/" + id + "/status");
}

export function deleteRecording(id: string) {
  return request<{ ok: boolean }>("/recordings/" + id, { method: "DELETE" });
}

export function deleteResume(id: string) {
  return request<{ ok: boolean }>("/resumes/" + id, { method: "DELETE" });
}

// Resumes
export function listResumes() {
  return request<Resume[]>("/resumes");
}

export function uploadResume(file: File) {
  const fd = new FormData();
  fd.append("file", file);
  return request<Resume>("/resumes/upload", { method: "POST", body: fd });
}

// Interview
export function analyzeInterview(recordingId: string, resumeId?: string) {
  return request<{ report_id: string; status: string; cached?: boolean }>("/interview/analyze", {
    method: "POST",
    body: JSON.stringify({ recording_id: recordingId, resume_id: resumeId || null }),
  });
}

export function listReports() {
  return request<InterviewReport[]>("/interview/reports");
}

export function getReport(id: string) {
  return request<InterviewReport>("/interview/reports/" + id);
}

// Conversation Summary
export function getRecordingSummary(id: string) {
  return request<import("../types").ConversationSummary>("/recordings/" + id + "/summary");
}

export function getRecordingQA(id: string) {
  return request<import("../types").ConversationQA>("/recordings/" + id + "/qa");
}

function buildAuthedDownloadUrl(path: string) {
  const token = getToken();
  if (!token) return BASE + path;
  const joiner = path.includes("?") ? "&" : "?";
  return BASE + path + joiner + "token=" + encodeURIComponent(token);
}

export function exportRecording(id: string, format: "txt" | "srt" | "docx" | "pdf") {
  window.open(buildAuthedDownloadUrl("/export/" + id + "/" + format), "_blank");
}

export function exportRecordingQA(id: string, format: "txt" | "docx" | "pdf") {
  window.open(buildAuthedDownloadUrl("/export/" + id + "/qa/" + format), "_blank");
}

export function exportReportPdf(id: string) {
  window.open(buildAuthedDownloadUrl("/export/report/" + id + "/pdf"), "_blank");
}

export function updateTranscript(id: string, content: string, speakerName?: string) {
  const fd = new FormData();
  fd.append("content", content);
  if (speakerName) fd.append("speaker_name", speakerName);
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = "Bearer " + token;
  return fetch(BASE + "/recordings/transcripts/" + id, { method: "PUT", body: fd, headers }).then((r) => r.json());
}
