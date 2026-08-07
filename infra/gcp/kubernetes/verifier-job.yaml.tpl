apiVersion: batch/v1
kind: Job
metadata:
  name: verifier-${JOB_ID}
  namespace: rl-env
  labels:
    app.kubernetes.io/name: rl-env-verifier
    rl-env/episode-id: ${JOB_ID}
spec:
  backoffLimit: 2
  ttlSecondsAfterFinished: 3600
  activeDeadlineSeconds: ${VERIFIER_TIMEOUT_SECONDS}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: rl-env-verifier
        rl-env/episode-id: ${JOB_ID}
        rl-env/trust: trusted
    spec:
      restartPolicy: Never
      serviceAccountName: verifier
      automountServiceAccountToken: false
      nodeSelector:
        rl-env/workload-class: trusted
      tolerations:
        - key: rl-env/trusted
          operator: Equal
          value: "true"
          effect: NoSchedule
      securityContext:
        runAsNonRoot: true
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: verifier
          # Verifier images and hidden tests must be digest pinned.
          image: ${VERIFIER_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: GOOGLE_CLOUD_PROJECT
              value: ${PROJECT_ID}
            - name: RL_ENV_EPISODE_ID
              value: ${JOB_ID}
            - name: RL_ENV_TASK_URI
              value: ${TASK_URI}
            - name: RL_ENV_AGENT_RESULT_URI
              value: ${AGENT_RESULT_URI}
            - name: RL_ENV_TRAJECTORY_BUCKET
              value: ${TRAJECTORY_BUCKET}
          resources:
            requests:
              cpu: 500m
              memory: 512Mi
            limits:
              cpu: "2"
              memory: 2Gi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 4Gi
