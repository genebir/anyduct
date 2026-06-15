{{- define "anyduct.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "anyduct.labels" -}}
app.kubernetes.io/part-of: anyduct
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "anyduct.jwtSecretName" -}}
{{- if .Values.jwt.existingSecret -}}
{{ .Values.jwt.existingSecret }}
{{- else -}}
{{ include "anyduct.fullname" . }}-jwt
{{- end -}}
{{- end -}}

{{- define "anyduct.databaseUrl" -}}
{{- if .Values.externalDatabase.url -}}
{{ .Values.externalDatabase.url }}
{{- else -}}
postgresql+asyncpg://{{ .Values.postgresql.auth.username }}:{{ .Values.postgresql.auth.password }}@{{ include "anyduct.fullname" . }}-db:5432/{{ .Values.postgresql.auth.database }}
{{- end -}}
{{- end -}}

{{/* The env block every server-image container shares — mirrors the
     compose file's x-server-env anchor. */}}
{{- define "anyduct.serverEnv" -}}
- name: DATABASE_URL
  value: {{ include "anyduct.databaseUrl" . | quote }}
- name: AUTH_JWT_PRIVATE_KEY_PEM
  valueFrom:
    secretKeyRef:
      name: {{ include "anyduct.jwtSecretName" . }}
      key: private.pem
- name: AUTH_JWT_PUBLIC_KEY_PEM
  valueFrom:
    secretKeyRef:
      name: {{ include "anyduct.jwtSecretName" . }}
      key: public.pem
- name: AUTH_JWT_ISSUER
  value: {{ .Values.env.jwtIssuer | quote }}
- name: AUTH_JWT_AUDIENCE
  value: {{ .Values.env.jwtAudience | quote }}
- name: CORS_ORIGINS
  value: {{ .Values.env.corsOrigins | quote }}
- name: SECRET_BACKEND
  value: {{ .Values.env.secretBackend | quote }}
- name: ENVIRONMENT
  value: {{ .Values.env.environment | quote }}
- name: SERVICE_NAME
  value: anyduct-server
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end -}}
