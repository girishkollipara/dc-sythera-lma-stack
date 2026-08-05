// LMA build/publish/deploy pipeline.
//
// Branch -> environment mapping (see D3 in CI-CD-PLAN.md for the open
// question on whether prod adopts the existing LMASA stack or a fresh
// LMASA-prod):
//   qa     -> qa
//   dev01  -> dev01
//   main   -> prod
//
// Any other branch runs build/lint only - publish/deploy are skipped, not
// failed, so feature branches still get CI feedback.
//
// Required per-environment Jenkins credentials (Kind: AWS Credentials):
//   aws-lma-qa, aws-lma-dev01, aws-lma-prod
// each scoped to deploy only its own LMASA-<env> stack.
//
// Required per-environment parameter files (not yet created - see the
// follow-up task): deploy/params/lma-<env>.json
//
// The 4 NoEcho secret parameters (TavilyApiKey, ElevenLabsApiKey,
// SimliApiKey, ZoomMeetingSdkClientSecret) are NOT set here and must not be
// added to the parameter files as plaintext. They resolve at deploy time via
// CloudFormation dynamic references (e.g.
// {{resolve:secretsmanager:lma/<env>/tavily-api-key}}) written directly into
// the parameter file - Jenkins never sees the plaintext value.
//
// The whole pipeline runs inside the image built from ci/Dockerfile (see
// that file) rather than directly on the Jenkins server - the server only
// needs Docker itself; every build tool (node, make, zip, python, aws cli,
// sam cli) lives in that image instead of being hand-installed on the host.
// Requires the "Docker Pipeline" Jenkins plugin. The docker.sock mount lets
// steps inside the container drive the host's Docker daemon (needed for the
// SAM/container build steps) without needing Docker-in-Docker.

def ENV_MAP = [
    'qa'   : 'qa',
    'dev01': 'dev01',
    'main' : 'prod',
]

pipeline {
    agent {
        dockerfile {
            filename 'ci/Dockerfile'
            dir '.'
            args '-v /var/run/docker.sock:/var/run/docker.sock'
        }
    }

    options {
        timeout(time: 60, unit: 'MINUTES')
        disableConcurrentBuilds()
    }

    environment {
        REGION = 'us-east-1'
        CFN_BUCKET_BASENAME = 'lma-artifacts'
        CFN_PREFIX = 'lma'
        // make setup-cli installs the `lma` CLI into .venv/bin, not
        // system-wide. Each pipeline stage is a fresh shell, so activating
        // the venv in one stage doesn't carry over to the next - putting
        // .venv/bin on PATH globally here means every later stage (Publish,
        // etc.) can find `lma` without needing to re-activate it each time.
        PATH = "${env.WORKSPACE}/.venv/bin:${env.PATH}"
    }

    stages {

        stage('Validate Branch Mapping') {
            steps {
                script {
                    env.DEPLOY_ENV = ENV_MAP[env.BRANCH_NAME]
                    if (env.DEPLOY_ENV) {
                        echo "Branch '${env.BRANCH_NAME}' maps to environment '${env.DEPLOY_ENV}' - publish/deploy will run."
                    } else {
                        echo "Branch '${env.BRANCH_NAME}' has no environment mapping - build/lint only, publish/deploy will be skipped."
                    }
                }
            }
        }

        stage('Check Environment') {
            steps {
                sh '''
                    echo "Checking required build tools on this agent (jenkins user, jenkins PATH)..."
                    MISSING=0
                    for cmd in bash node npm docker zip python3 pip3 virtualenv aws sam make; do
                        if command -v "$cmd" >/dev/null 2>&1; then
                            echo "OK       $cmd -> $(command -v $cmd) ($($cmd --version 2>&1 | head -1))"
                        else
                            echo "MISSING  $cmd"
                            MISSING=1
                        fi
                    done
                    if [ "$MISSING" -eq 1 ]; then
                        echo "One or more required tools are missing on this Jenkins agent - see CLAUDE.md for the full prerequisite list."
                        exit 1
                    fi
                '''
            }
        }

        stage('Setup CLI') {
            steps {
                // setup-cli alone assumes .venv already exists - setup-python
                // is what actually creates it (make setup-cli's own error,
                // "No such file or directory" on .venv/bin/pip, is exactly
                // this - the venv was never created first).
                sh 'make setup-python setup-cli'
            }
        }

        stage('Build') {
            steps {
                sh 'make build'
            }
        }

        stage('Publish') {
            when { expression { env.DEPLOY_ENV != null } }
            steps {
                sh "./publish.sh ${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV} ${CFN_PREFIX} ${REGION}"
            }
        }

        stage('Deploy') {
            when { expression { env.DEPLOY_ENV != null } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh """
                        aws cloudformation deploy \
                          --region ${REGION} \
                          --stack-name LMASA-${env.DEPLOY_ENV} \
                          --template-file lma-main.yaml \
                          --parameter-overrides file://deploy/params/lma-${env.DEPLOY_ENV}.json \
                          --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
                          --no-fail-on-empty-changeset
                    """
                }
            }
        }

        stage('Verify') {
            when { expression { env.DEPLOY_ENV != null } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh """
                        STATUS=\$(aws cloudformation describe-stacks \
                          --region ${REGION} \
                          --stack-name LMASA-${env.DEPLOY_ENV} \
                          --query 'Stacks[0].StackStatus' --output text)
                        echo "Stack status: \$STATUS"
                        case "\$STATUS" in
                          CREATE_COMPLETE|UPDATE_COMPLETE) exit 0 ;;
                          *) echo "Stack did not reach a healthy COMPLETE status"; exit 1 ;;
                        esac
                    """
                }
            }
        }

        stage('Export Outputs to SSM') {
            when { expression { env.DEPLOY_ENV != null } }
            steps {
                withCredentials([[
                    $class: 'AmazonWebServicesCredentialsBinding',
                    credentialsId: "aws-lma-${env.DEPLOY_ENV}",
                ]]) {
                    sh "./scripts/export_stack_resources_to_ssm.sh LMASA-${env.DEPLOY_ENV} ${env.DEPLOY_ENV} ${REGION} ${CFN_BUCKET_BASENAME}-${env.DEPLOY_ENV}-${REGION}"
                }
            }
        }
    }

    post {
        always {
            echo "Pipeline finished for branch ${env.BRANCH_NAME} (environment: ${env.DEPLOY_ENV ?: 'none - build/lint only'})"
        }
    }
}
