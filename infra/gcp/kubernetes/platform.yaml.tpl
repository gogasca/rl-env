apiVersion: v1
kind: Namespace
metadata:
  name: rl-env
  labels:
    app.kubernetes.io/name: rl-env
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: controller
  namespace: rl-env
  annotations:
    iam.gke.io/gcp-service-account: ${CONTROLLER_GSA}
automountServiceAccountToken: false
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: verifier
  namespace: rl-env
  annotations:
    iam.gke.io/gcp-service-account: ${VERIFIER_GSA}
automountServiceAccountToken: false
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: agent
  namespace: rl-env
automountServiceAccountToken: false
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: default-deny
  namespace: rl-env
spec:
  podSelector: {}
  policyTypes:
    - Ingress
    - Egress
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: trusted-google-apis-egress
  namespace: rl-env
spec:
  podSelector:
    matchLabels:
      rl-env/trust: trusted
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
    - ports:
        - protocol: TCP
          port: 443
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: controller
  namespace: rl-env
  labels:
    app.kubernetes.io/name: rl-env-controller
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: rl-env-controller
  template:
    metadata:
      labels:
        app.kubernetes.io/name: rl-env-controller
        rl-env/trust: trusted
    spec:
      serviceAccountName: controller
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
        - name: controller
          image: ${CONTROLLER_IMAGE}
          imagePullPolicy: IfNotPresent
          env:
            - name: GOOGLE_CLOUD_PROJECT
              value: ${PROJECT_ID}
            - name: RL_ENV_JOBS_TOPIC
              value: ${JOBS_TOPIC}
            - name: RL_ENV_JOBS_SUBSCRIPTION
              value: ${JOBS_SUBSCRIPTION}
            - name: RL_ENV_REVIEW_TOPIC
              value: ${REVIEW_TOPIC}
            - name: RL_ENV_TRAJECTORY_BUCKET
              value: ${TRAJECTORY_BUCKET}
          resources:
            requests:
              cpu: 250m
              memory: 256Mi
            limits:
              cpu: "1"
              memory: 1Gi
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
            sizeLimit: 2Gi
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: controller
  namespace: rl-env
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: rl-env-controller
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: controller
  namespace: rl-env
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: controller
  minReplicas: 2
  maxReplicas: 20
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
