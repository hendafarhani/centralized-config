package com.micro_services.centralized_config;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.cloud.config.environment.Environment;
import org.springframework.cloud.config.server.environment.EnvironmentRepository;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(properties = {
        "spring.cloud.bus.enabled=false",
        "spring.cloud.config.server.native.search-locations=file:centralized-configuration"
})
class CentralizedConfigApplicationTests {

    @Autowired
    private EnvironmentRepository environmentRepository;

    @Test
    void loadsRideRequestNativeConfiguration() {
        Environment environment = environmentRepository.findOne("ride-request", "default", null);

        assertThat(environment.getPropertySources()).isNotEmpty();
        assertThat(environment.getPropertySources())
                .anySatisfy(source -> {
                    assertThat(source.getSource().get("spring.application.name")).isEqualTo("ride-request");
                    assertThat(source.getSource().get("kafka.bootstrap-servers")).isEqualTo("kafka:9092");
                });
    }
}
