apiVersion: batch/v1
kind: Job
metadata:
  name: agent-${JOB_ID}
  namespace: rl-env
  labels:
    app.kubernetes.io/name: rl-env-agent
    rl-env/episode-id: ${JOB_ID}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 900
  activeDeadlineSeconds: ${EPISODE_TIMEOUT_SECONDS}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: rl-env-agent
        rl-env/episode-id: ${JOB_ID}
        rl-env/network-profile: restricted-google
    spec:
      restartPolicy: Never
      runtimeClassName: gvisor
      serviceAccountName: agent
      automountServiceAccountToken: false
      nodeSelector:
        rl-env/workload-class: sandbox
      tolerations:
        - key: rl-env/sandbox
          operator: Equal
          value: "true"
          effect: NoSchedule
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: agent
          # Admission policy should require an @sha256 digest here.
          image: ${AGENT_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: RL_ENV_TASK_URI
              value: ${TASK_URI}
            - name: RL_ENV_RESULT_UPLOAD_URL
              valueFrom:
                secretKeyRef:
                  name: ${RESULT_URL_SECRET}
                  key: upload-url
          resources:
            requests:
              cpu: ${CPU_REQUEST}
              memory: ${MEMORY_REQUEST}
            limits:
              cpu: ${CPU_LIMIT}
              memory: ${MEMORY_LIMIT}
              ephemeral-storage: ${DISK_LIMIT}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: workspace
              mountPath: /workspace
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: workspace
          emptyDir:
            sizeLimit: ${DISK_LIMIT}
        - name: tmp
          emptyDir:
            sizeLimit: 512Mi
---
# Agent pods have no cloud identity. This grants only DNS and Google's
# restricted API VIP; the per-episode signed URL scopes the writable object.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agent-restricted-google-egress
  namespace: rl-env
spec:
  podSelector:
    matchLabels:
      rl-env/network-profile: restricted-google
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - ipBlock:
            cidr: 199.36.153.4/30
      ports:
        - protocol: TCP
          port: 443
