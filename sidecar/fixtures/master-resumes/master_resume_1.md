# Tenet Loader

**Headline:** Forward deployed engineer with experience in building distributed backend applications and AI-driven applications.

- **Email:** [tenetloader@gmail.com](mailto:tenetloader@gmail.com)
- **Phone:** +91 9999999999
- **Location:** Mumbai, India
- **LinkedIn:** linkedin.com/in/tenet-loader/
- **GitHub:** github.com/tenetloader

---

## Summary

- 7+ years of experience designing and building large-scale distributed systems and AI-driven applications
- Available to start immediately
- Open to relocation within India

---

## Skills

- **Languages:** Java, TypeScript, JavaScript, Python, Rust, Swift, C++, Ruby, Bash
- **Frameworks:** Spring, Spring Boot, Spring Security (LDAP, OAuth2, SAML, JWT, PKI), React, Next.js, FastAPI, Flask, Tauri, SwiftUI, WidgetKit, PySpark, STL
- **AI/LLM:** LLM agent orchestration, MCP, RAG, embeddings & semantic search (Sentence Transformers, pgvector), Anthropic, OpenAI, OpenRouter, Ollama
- **Cloud (AWS):** S3, EC2, ECS, Fargate, Lambda, DynamoDB, CloudWatch, Glue
- **APIs:** REST, GraphQL, SOAP, WSDL, SCIM
- **Tools:** Git, Docker, Terraform, Kafka, SQLite
- **Legacy Java EE:** JSP, Servlets, JavaBeans, WebSphere

---

## Experience

### srini404.com - Private consulting and personal projects

- **Dates:** Aug 2024 – Present
- **Location:** Mumbai
- **Role:** Software Engineer
- **Tech:** Python, FastAPI, Rust, Tauri, React, Next.js, TypeScript, SQLite, AWS, Docker, Swift/SwiftUI (iOS, macOS)

##### Project: FindsYouJobs

Open-source (MIT), local-first AI job-search desktop app — Tauri (Rust) shell, Python FastAPI sidecar, React + TypeScript frontend, local SQLite, BYOK LLM providers (Anthropic, OpenAI, OpenRouter, Ollama).

- Designed human-centric LLM agents behind a modular agent registry: job scoring, resume and cover-letter tailoring grounded to the user's master resume, and referral outreach — every irreversible action gated by per-action human confirmation.
- Shipped a scored daily job feed (embeddings + semantic search), a pipeline tracker (Saved → Applied → Interviewing → Offer), a copilot grounded in the user's own data, and an LLM cost/usage dashboard with per-step model routing (cheap models for filtering, strong models for human-facing output).
- Built a re-runnable eval harness with published baselines to measurably drive down hallucination and fabrication in tailored output.
- Evolved to an agent-centric workflow : built an MCP server enabling Hermes-driven autonomous operation — the agent finds, applies, and networks within user-set criteria and reports back its actions.

##### Project: LeetCodeLytics

Native LeetCode companion for iPhone, iPad, and Mac — Swift, SwiftUI, WidgetKit.

- Tracks solved counts by difficulty, acceptance rate, and streaks, with a year-long activity heatmap and a badge system.
- Built home-screen and lock-screen widgets and topic-based practice recommendations.

##### Project: srini404.com

Personal website

- Created website using Next.js

### Salesforce — Senior Member of Technical Staff

- **Dates:** Apr 2022 – Aug 2024
- **Location:** Seattle
- **Role:** Full-stack software engineer with focus on backend development
- **Team:** Tableau Online Identity and Authentication
- **Team description:** Responsible for the frontend and backend for sso.online.tableau.com landing page and Settings page after successful login. All users of Tableau Online are thus authenticated and managed by the services (SiteSAML, MIDS and SCIM) the team owns.
- **Tech:** Java 8, Java 17, Spring Security, React, Figma, DynamoDB, Terraform (TypeScript), ECS, Fargate, Lambda (Python and JavaScript), CloudWatch

##### Project: Link

Tableau introduced the concept of Link, which is another classification after Users and Groups. An organization can have 1 or multiple Links, with each Link containing a disjoint set of Users and Groups.

- Created Figma specifications for a new page with multiple operations for Link and implemented it using React.
- Developed new clients for the Link UI to add and modify users and groups to Link, and to select multiple users to modify their authentication type.
- Mentored a junior dev to develop few clients for the Link UI.
- Persisted all information in existing DynamoDB tables and a few new ones.
- Made changes in managed identity services like MIDS and SCIM to consume some of these new clients.
- The clients used the Java Spring Security framework.

##### Project: Telemetry migration

Salesforce-wide migration to Salesforce internal OpenTelemetry-based tool.

- Led the migration for all the microservices in the Tableau Online space.
- Architected and implemented a multi-part solution to migrate from New Relic to Salesforce internal tooling while meeting multiple conflicting constraints such as latency, cost, and deadline.
- Wrote an agent to write telemetry to logs and added a Lambda to read logs and emit to a Kafka queue that sends to multiple target endpoints as a stop-gap solution.
- Later refined the agent to send directly to the target endpoints.

##### Responsibilities

- Owned the entire lifecycle of the projects - Design, Develop, Test, Code Review, Deploy. Also supported as Oncall for all the services the team owned.
- Worked with authentication and identity management technologies like OAuth, SAML, PKI to support authentication, authorization, and user management workflows.
- Performed operational tasks as part of on-call duties, bug fixes, and software campaigns.
- Mentored a junior dev

---

### Amazon — Software Development Engineer I

- **Dates:** Aug 2019 – Apr 2022
- **Location:** Seattle
- **Role:** Backend software engineer
- **Team:** AWS S3 Storage Control Plane, Volume Placement
- **Team description:** Responsible for creating and maintaining logical abstraction (Volume) for the objects to be stored in. Volumes ensure the availability, the latency and most importantly the durability SLAs promised by AWS S3 for all its APIs. Team owned multiple Java Spring services to create, maintain (migrate, repair) and update volume information.
- **Tech:** Java 8, Java 11, Spring, DynamoDB, Pipelines (Ruby), EC2, Lambda (Python), CloudWatch

##### Project: New racks data migration efficiency improvement

New racks landing on site need existing data to be migrated to them to prevent them from getting thrashed with new traffic. The speed of this data migration was historically constrained by network devices, specifically the TOR of a rack. Traditionally the speed was determined per rack type. With the advent of higher-capacity TOR, host disk speed became the bottleneck.

- Led and designed a solution to dynamically scale migration speeds.
- Consumed TOR specifications and max disk read speed from an API maintained by the Capacity team.
- Introduced the logic to maximize the speeds using Java Spring.
- Created a workflow for analyzing new storage racks' balancing costs using PySpark and AWS Glue.
- Improved storage host balancing efficiency by 50%, amounting to 2 million dollar savings.

##### Project: Failover system for a dynamic config update system for 75k hosts

A static config determined how much traffic percentage is routed to different Volume worlds (similar to Availability Zones) in a region. In the event a volume world was down or overwhelmed, there was no dynamic way to stop routing traffic completely or partially without a manual code deployment to update configs. A dynamic config update system was in progress, but because it was in the critical request path, a break-glass tool was needed in case the new system failed at any point in the multi-part development process.

- Created a tool consisting of a few Python scripts with the main goal of updating the config files on disk of the 75k hosts by remotely SSH-ing into each of them.
- Built the tool to run with minimal manual work.
- Added support for printing stats after the tool runs.
- Added the ability to retry failed deployments.

##### Responsibilities

- Owned the entire lifecycle of the projects - Design, Develop, Test, Code Review, Deploy. Also supported as Oncall for all the services the team owned.
- Performed operational tasks as part of on-call duties, bug fixes, and software campaigns.

---

### University at Buffalo — Graduate Student Assistant

- **Dates:** May 2018 – Jun 2019
- **Location:** Buffalo
- **Role:** Researcher under Dr. Chunming Qiao
- **Team:** Connected and Autonomous Vehicle Applications and Systems
- **Team description:** Team concentrated on multiple initiatives in the self-driving car space. One initiative was to create a reliable simulation framework to evaluate various open source self-driving car software like Autoware and comma.ai. Another was to set up different open source software in the university's newly acquired self-driving car hardware (Lincoln installed with all the sensor, actuator and computer suites).
- **Tech:** Unity, C#, ROS, Autoware

##### Project: Connected and autonomous vehicle simulation and deployment work

- Modeled LiDAR sensor output in the Unity 3D game engine using the depth buffer, employing C# and ROS.
- Configured a self-driving car robot URDF and simulated part of the real world in Gazebo.
- Executed Gmapping SLAM and Hector SLAM using the modeled LiDAR and the simulated world.
- Performed part of the Autoware installation process into the Lincoln.

##### Responsibilities

- Owned and delivered several aspects within the team's scope.
- Duties ranged from developing code to helping deploy open source software.

---

### Buffalo Automation — Software Developer Intern

- **Dates:** Jun 2018 – Dec 2018
- **Location:** Buffalo
- **Role:** Intern
- **Team description:** Company provides software solutions to ships.
- **Tech:** Python (Flask), ROS, Unity, C#

##### Project: LiDAR stream processing and output

- Merged multiple LiDAR sensor streams using the ROS tf package in C++.
- Developed a LiDAR sensor streaming output using Python Flask.

##### Responsibilities

- Owned and delivered several aspects within the team's scope.
- Duties ranged from developing prototypes in Unity to processing sensor signals.

---

### Accenture — Applications Development Engineer

- **Dates:** Aug 2015 – Jul 2017
- **Location:** Mumbai
- **Client:** BestBuy Inc.
- **Role:** Backend software engineer
- **Team:** POS - Message Broker
- **Tech:** IBM WebSphere Message Broker, Java, WSDL, IBM WebSphere Message Queue, Jenkins CI/CD

##### Project: POS middleware and timezone parsing fix

- Coded, tested, documented and maintained Message Flows and assisted the support team in Code Deployment.
- Integrated with databases, SOAP and REST services and worked on data formats like XML and flat-files.
- Led the encryption key management vendor change for the middleware framework.
- Debugged an issue where downstream systems were not receiving certain messages after Daylight Saving Time started.
- Found and implemented a fix so the system parsed not just Central Time but every possible timezone effectively.
- Fixed the failure rate and increased message processing speed by 70%.

##### Responsibilities

- Develop and maintain features on POS systems by providing all downstream systems the required data.

---

## Education

### University at Buffalo — M.Sc. in Computer Science

- **Dates:** 2017 – 2019
- **Location:** NY, USA
- **GPA:** 3.63

### Mumbai University — B.E. in Computer Engineering

- **Dates:** 2011 – 2015
- **Location:** India
- **Honors:** Graduated with Distinction

---

## Projects

### Robot state estimation using Bayes filter

- **Date:** Aug 2018
- **Tech:** Python, ROS

Highlights:

- Implemented the algorithm to estimate robot state given the sensor measurements.
- Used a velocity-based sensor model for the Bayes Filter implementation.

### Image Stitching using Homography Matrix Estimation

- **Date:** Nov 2017
- **Tech:** MATLAB

Highlights:

- Estimated homography matrix using the RANSAC algorithm to determine similar points in two images to be stitched.
- Estimated the fundamental matrix and performed triangulation.

### Scene Classification System using Bag-of-words approach

- **Date:** Oct 2017
- **Tech:** MATLAB

Highlights:

- Used Spatial Pyramid Matching with Bag-of-words to determine object features.
- Used 1350 images to train the model.
- Obtained test accuracy of 56 percent on a dataset of 1500 images.

### Simulation of transport layer protocols in Computer Networks

- **Date:** Sep 2017
- **Tech:** C++

Highlights:

- Simulated Alternating Bit, Go-Back-N and Selective Repeat protocols.
- Analyzed and compared the protocols for the efficiency in sending messages between two hosts.

### Driver Evaluation System using Mobile Phone & OBD-II System

- **Dates:** Aug 2014 – Apr 2015
- **Tech:** Android, PHP, MySQL

Highlights:

- Developed an Android app that determined how well people drive using information from sensors in cars and mobile phone.
- Made driving scores available for third-party organizations like DMV and driving schools.
- Was responsible for conceptualizing the architecture and data flow and implementing the backend server processing.
- Published in International Journal of Computer Science and Information Technologies (IJCSIT).
- Project selected for the final round of SPARKLE 2015, a national level innovation competition by KPIT Technologies.

---

## Achievements

#### Won 1st prize in the AWS S3 org wide hackathon

- **Date:** 2022

Highlights:

- 30 engineers participated in the hackathon.
- Developed a service that provides an overview of all the active instances of a recently horizontally scaled service.
- Used the existing gossip protocol setup used to discover the storage instances.

#### Finalist in ConsenSys and Loom Hackathon

- **Date:** 2018

Highlights:

- Among the Top 3 of 20 teams participating in the hackathon. It was an international level hackathon organized at NYC.
- Developed a NFT based Collectible Card Game in Unity using Loom SDK.
- The SDK enabled side chain based application development.

#### 2nd in 3 minute talk organized at University at Buffalo

- **Date:** 2018

Highlights:

- Presented research on LiDAR Point Cloud processing and simulation.

#### Finalist in a national level innovation competition - KPIT Technologies's Sparkle

- **Date:** 2015

Highlights:

- Among the Top 20 of 1000 odd teams participating in the competition.
- Presented the Driver Evaluation System using Mobile Phone & OBD-II System.

#### Global finalist in Hack4Good hackathon organized by Google Developer Group and Geeklist

- **Date:** 2014

Highlights:

- Selected in the Top 35 from 145 teams.
- Part of a 3-person team and developed a PHP backend and SQL server that stored information about planted trees and their exact locations to help track the status of planted trees.

