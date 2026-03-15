package com.micro_services.centralized_config;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.config.server.EnableConfigServer;

@EnableConfigServer
@SpringBootApplication
public class CentralizedConfigApplication {

	static void main(String[] args) {
		SpringApplication.run(CentralizedConfigApplication.class, args);
	}

}
