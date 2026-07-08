<?php

declare(strict_types=1);

namespace Laminas\Hydrator;

/**
 * @deprecated Since 4.17.0 This interface will be removed in version 5.0 without replacement.
 */
interface HydratorProviderInterface
{
    /**
     * Provide plugin manager configuration for hydrators.
     *
     * @see    https://docs.mezzio.dev/mezzio/v3/features/container/config/#the-format
     *
     * @return mixed[][]
     */
    public function getHydratorConfig(): array;
}
