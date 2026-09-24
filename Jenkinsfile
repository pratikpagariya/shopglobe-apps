pipeline {
  // Runs on the kaniko pod template: kaniko + tools + trivy containers.
  agent { label 'kaniko' }

  options {
    timeout(time: 30, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  environment {
    REGISTRY  = '355653384401.dkr.ecr.ap-south-1.amazonaws.com'
    REGION    = 'ap-south-1'
    // Tag = git SHA. Not :latest, not a version you type. This is what makes
    // "which code is running?" answerable, and it is why ECR is IMMUTABLE.
    IMAGE_TAG = "${env.GIT_COMMIT?.take(12) ?: 'dev'}"
    REPO      = "shopglobe-prod-${params.SERVICE}"
  }

  stages {

    stage('lint') {
      steps {
        container('tools') {
          sh '''
            # Python packages use underscores -- module names cannot contain
            # hyphens. ECR repos, k8s Services and DNS labels cannot contain
            # underscores. Same service, two spellings. Translate explicitly
            # rather than letting the mismatch surface as a missing path.
            SVC_DIR=$(echo "${SERVICE}" | tr '-' '_')
            pip install --quiet ruff
            ruff check services/${SVC_DIR} libs/
            ruff format --check services/${SVC_DIR} libs/
          '''
        }
      }
    }

    stage('test') {
      steps {
        container('tools') {
          sh '''
            pip install --quiet -r requirements-dev.txt
            pytest -q --junitxml=report.xml
          '''
        }
      }
      post {
        always { junit allowEmptyResults: true, testResults: 'report.xml' }
      }
    }

    stage('build + push') {
      steps {
        container('kaniko') {
          sh '''
            # Kaniko authenticates to ECR through the credential helper, which
            # reads the ambient credentials Pod Identity gave this pod. No
            # docker login, no registry password, no static keys anywhere.
            mkdir -p /kaniko/.docker
            cat > /kaniko/.docker/config.json <<EOF
            {"credHelpers":{"${REGISTRY}":"ecr-login"}}
EOF
            /kaniko/executor \
              --context=$(pwd) \
              --dockerfile=Dockerfile \
              --destination=${REGISTRY}/${REPO}:${IMAGE_TAG} \
              --cache=true \
              --cache-repo=${REGISTRY}/${REPO} \
              --snapshot-mode=redo
          '''
        }
      }
    }

    stage('scan') {
      steps {
        container('trivy') {
          // Fail on HIGH/CRITICAL that actually have a fix available.
          // Blocking on unfixable CVEs teaches people to disable scanning,
          // which is worse than the CVE.
          sh '''
            trivy image --quiet --exit-code 1 \
              --severity HIGH,CRITICAL --ignore-unfixed \
              --ignorefile .trivyignore \
              ${REGISTRY}/${REPO}:${IMAGE_TAG}
          '''
        }
      }
    }

    stage('deploy: commit tag to gitops') {
      when { expression { params.DEPLOY } }
      steps {
        container('tools') {
          withCredentials([usernamePassword(
              credentialsId: 'github',
              usernameVariable: 'GH_USER',
              passwordVariable: 'GH_TOKEN')]) {
            sh '''
              apt-get update -qq && apt-get install -y -qq git curl
              curl -sL -o /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64
              chmod +x /usr/local/bin/yq

              rm -rf /tmp/gitops
              git clone -q https://${GH_USER}:${GH_TOKEN}@github.com/${GH_USER}/shopglobe-gitops.git /tmp/gitops
              cd /tmp/gitops

              # THIS LINE IS THE DEPLOY. Not kubectl apply. Not helm --set.
              # A commit. ArgoCD sees the diff and reconciles the cluster.
              # Which means: git log IS the deploy history, and git revert IS
              # the rollback.
              mkdir -p values/dev/${SERVICE}
              yq -i ".image.tag = \\"${IMAGE_TAG}\\"" values/dev/${SERVICE}/values.yaml 2>/dev/null \
                || printf 'image:\\n  tag: "%s"\\n' "${IMAGE_TAG}" > values/dev/${SERVICE}/values.yaml

              git config user.email "jenkins@shopglobe.local"
              git config user.name  "jenkins"
              git add values/dev/${SERVICE}/values.yaml
              git diff --cached --quiet && echo "no change" && exit 0
              git commit -m "deploy(${SERVICE}): ${IMAGE_TAG} [ci skip]"
              git push
            '''
          }
        }
      }
    }
  }

  post {
    success { echo "${params.SERVICE} @ ${IMAGE_TAG} built, scanned and committed to gitops" }
    failure { echo "FAILED at stage: ${env.STAGE_NAME}" }
  }
}
