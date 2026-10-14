pipeline {
  // Runs on the kaniko pod template: kaniko + tools + trivy + yq containers,
  // plus the auto-injected jnlp container, which has git.
  agent { label 'kaniko' }

  options {
    timeout(time: 30, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  environment {
    REGISTRY    = '355653384401.dkr.ecr.ap-south-1.amazonaws.com'
    REGION      = 'ap-south-1'
    GITOPS_REPO = 'https://github.com/pratikpagariya/shopglobe-gitops.git'
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

            # Pinned versions from requirements-dev.txt -- the same ruff and
            # mypy the laptop runs. An unpinned `pip install ruff` lets CI and
            # the laptop disagree about whether the same code is clean.
            # Installed once; the test stage reuses this container.
            pip install --quiet -r requirements-dev.txt
            ruff check services/${SVC_DIR} libs/
            ruff format --check services/${SVC_DIR} libs/
            mypy libs services/${SVC_DIR}
          '''
        }
      }
    }

    stage('test') {
      steps {
        container('tools') {
          // The coverage threshold lives in pyproject.toml, not here, so
          // `make test` on a laptop fails exactly the way CI does.
          sh 'pytest --junitxml=report.xml'
        }
      }
      post {
        always { junit allowEmptyResults: true, testResults: 'report.xml' }
      }
    }

    stage('image exists?') {
      steps {
        container('tools') {
          script {
            // ECR tags are IMMUTABLE, so re-running a build of a commit that
            // was already pushed fails at push. Same SHA = same image: skip the
            // build, still scan, still deploy. Exit 2 = the check itself broke
            // (credentials, IAM) -- fail loudly rather than guess.
            def rc = sh(returnStatus: true, script: 'python scripts/image_exists.py')
            if (rc > 1) { error('scripts/image_exists.py failed -- see its stderr above') }
            env.IMAGE_EXISTS = (rc == 0) ? 'true' : 'false'
            if (env.IMAGE_EXISTS == 'true') { echo "${REPO}:${IMAGE_TAG} already in ECR -- skipping build" }
          }
        }
      }
    }

    stage('build + push') {
      when { expression { env.IMAGE_EXISTS != 'true' } }
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
        withCredentials([usernamePassword(
            credentialsId: 'github',
            usernameVariable: 'GH_USER',
            passwordVariable: 'GH_TOKEN')]) {

          // Every tool in this stage comes from a pinned image. No apt-get,
          // no `curl .../releases/latest` on every build.
          container('jnlp') {
            sh '''
              rm -rf gitops
              # The token is read from the environment by a credential helper,
              # only at the moment git asks -- never in the clone URL (visible
              # in `ps`) and never written to .git/config.
              git config --global credential.helper '!f() { echo "username=${GH_USER}"; echo "password=${GH_TOKEN}"; }; f'
              git config --global user.email "jenkins@shopglobe.local"
              git config --global user.name  "jenkins"
              git clone -q "${GITOPS_REPO}" gitops
            '''
          }

          container('yq') {
            sh '''
              F="gitops/values/dev/${SERVICE}/values.yaml"
              # A missing file is a misconfiguration, not something CI should
              # invent: the chart needs module, repository and env that only a
              # human can write. Fail loudly instead of writing half a file.
              test -f "$F" || { echo "ERROR: $F missing -- add it to shopglobe-gitops first"; exit 1; }

              # THIS LINE IS THE DEPLOY. Not kubectl apply. Not helm --set.
              # A commit. ArgoCD sees the diff and reconciles the cluster.
              # Which means: git log IS the deploy history, and git revert IS
              # the rollback.
              yq -i '.image.tag = strenv(IMAGE_TAG)' "$F"
            '''
          }

          container('jnlp') {
            sh '''
              cd gitops
              git add "values/dev/${SERVICE}/values.yaml"
              git diff --cached --quiet && { echo "already at ${IMAGE_TAG} -- nothing to deploy"; exit 0; }
              git commit -q -m "deploy(${SERVICE}): ${IMAGE_TAG} [ci skip]"

              # Eight service jobs push to ONE repo. disableConcurrentBuilds only
              # serialises builds of the SAME job, so a libs/common change that
              # rebuilds all 8 makes them race here. Each touches a different
              # file, so rebase-and-retry always resolves cleanly.
              for i in 1 2 3 4 5; do
                git push -q origin HEAD:main && exit 0
                echo "push rejected (another service got there first) -- rebase, attempt $i"
                sleep $((i * 2))
                git pull -q --rebase origin main
              done
              echo "ERROR: could not push after 5 attempts"; exit 1
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
