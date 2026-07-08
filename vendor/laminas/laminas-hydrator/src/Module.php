<?php

declare(strict_types=1);

namespace Laminas\Hydrator;

use Laminas\ModuleManager\ModuleManager;

class Module
{
    /**
     * Return default laminas-hydrator configuration for laminas-mvc applications.
     *
     * @return mixed[]
     */
    public function getConfig(): array
    {
        $provider = new ConfigProvider();

        return [
            'service_manager' => $provider->getDependencyConfig(),
        ];
    }

    /**
     * Register a specification for the HydratorManager with the ServiceListener.
     *
     * @deprecated Since 4.17.0 This method is not necessary for module manager and will be removed in 5.0
     */
    public function init(ModuleManager $moduleManager): void
    {
        $event           = $moduleManager->getEvent();
        $container       = $event->getParam('ServiceManager');
        $serviceListener = $container->get('ServiceListener');

        $serviceListener->addServiceManager(
            'HydratorManager',
            'hydrators',
            HydratorProviderInterface::class,
            'getHydratorConfig'
        );
    }
}
